"""
client_handler.py — Per-client TLS connection handler
──────────────────────────────────────────────────────
Runs in its own thread.  Reads JSON commands from the
client over a TLS socket and dispatches them.  Sends
question broadcasts, answer results, and leaderboard
updates back to the client.
"""

import json
import logging
import ssl
import socket
import time
import threading
from typing import Callable, Optional

from session_manager import SessionManager

logger = logging.getLogger("quiz.client_handler")


class ClientHandler(threading.Thread):
    """
    Handles a single client's TLS socket connection.

    Lifecycle:
        1. TLS handshake is already completed before __init__
        2. run()  — waits for 'join', then enters message loop
        3. Client sends JSON commands; handler dispatches them
        4. Server-side broadcasts call send_message() from other threads
    """

    RECV_BUFFER = 4096

    def __init__(
        self,
        client_socket: ssl.SSLSocket,
        client_address: tuple,
        session: SessionManager,
        on_disconnect: Callable[["ClientHandler"], None],
    ) -> None:
        super().__init__(daemon=True)
        self.sock = client_socket
        self.address = client_address
        self.session = session
        self._on_disconnect = on_disconnect

        self.username: Optional[str] = None
        self._send_lock = threading.Lock()
        self._running = True

        # Log TLS handshake details
        self._log_tls_info()

    # ── TLS info logging ──────────────────────────────────────

    def _log_tls_info(self) -> None:
        """Log SSL/TLS connection details for demonstration."""
        try:
            cipher = self.sock.cipher()
            tls_version = self.sock.version()
            peer_cert = self.sock.getpeercert()

            logger.info(
                "TLS handshake complete for %s:%d",
                self.address[0], self.address[1],
            )
            logger.info("  TLS Version : %s", tls_version)
            logger.info(
                "  Cipher Suite: %s (bits=%s, protocol=%s)",
                cipher[0] if cipher else "N/A",
                cipher[2] if cipher else "N/A",
                cipher[1] if cipher else "N/A",
            )
            if peer_cert:
                logger.info("  Peer Cert   : %s", peer_cert)
            else:
                logger.info("  Peer Cert   : (no client cert — server-only auth)")

            self._tls_version = tls_version or "unknown"
            self._cipher = cipher[0] if cipher else "unknown"
        except Exception as exc:
            logger.warning("Could not retrieve TLS info: %s", exc)
            self._tls_version = "unknown"
            self._cipher = "unknown"

    # ── Network I/O helpers ───────────────────────────────────

    def send_message(self, msg: dict) -> bool:
        """Thread-safe: serialize dict to JSON + newline, send over TLS."""
        with self._send_lock:
            try:
                data = json.dumps(msg) + "\n"
                self.sock.sendall(data.encode("utf-8"))
                return True
            except (OSError, ssl.SSLError) as exc:
                logger.debug("Send failed to %s: %s", self.username, exc)
                return False

    def _recv_line(self) -> Optional[str]:
        """Block until a full newline-terminated JSON message arrives."""
        buffer = b""
        while self._running:
            try:
                chunk = self.sock.recv(self.RECV_BUFFER)
                if not chunk:
                    return None          # client closed connection
                buffer += chunk
                if b"\n" in buffer:
                    line, _ = buffer.split(b"\n", 1)
                    return line.decode("utf-8")
            except socket.timeout:
                continue
            except (OSError, ssl.SSLError):
                return None
        return None

    # ── Main thread loop ──────────────────────────────────────

    def run(self) -> None:
        """Entry point for the client handler thread."""
        try:
            self.sock.settimeout(1.0)       # 1s timeout for clean shutdown
            self._message_loop()
        except Exception as exc:
            logger.error("Client %s error: %s", self.username or self.address, exc)
        finally:
            self._cleanup()

    def _message_loop(self) -> None:
        """Read JSON lines from the client and dispatch actions."""
        while self._running:
            raw = self._recv_line()
            if raw is None:
                logger.info("Client %s disconnected.", self.username or self.address)
                return

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                self.send_message({"type": "error", "message": "invalid_json"})
                continue

            action = msg.get("action", "")
            self._dispatch(action, msg)

    def _dispatch(self, action: str, msg: dict) -> None:
        """Route an incoming action to the appropriate handler."""
        if action == "join":
            self._handle_join(msg)
        elif action == "submit_answer":
            self._handle_submit_answer(msg)
        elif action == "start_quiz":
            self._handle_start_quiz()
        elif action == "ping":
            self.send_message({"type": "pong", "server_ts": time.time()})
        else:
            self.send_message({"type": "error", "message": "unknown_action"})

    # ── Action handlers ───────────────────────────────────────

    def _handle_join(self, msg: dict) -> None:
        """Register the client as a quiz participant."""
        raw_name = str(msg.get("username", "anonymous")).strip()[:32]
        is_host = bool(msg.get("host", False))

        self.username = raw_name
        status = self.session.add_participant(raw_name, is_host=is_host)

        self.send_message({
            "type": "welcome",
            "username": self.username,
            "status": status,
            "host": self.session.host,
            "tls_version": self._tls_version,
            "cipher": self._cipher,
            "participants": self.session.get_connected_count(),
            "quiz_started": self.session.started,
            "quiz_finished": self.session.finished,
        })

        logger.info(
            "Player '%s' %s from %s:%d  [TLS %s | %s]",
            self.username, status,
            self.address[0], self.address[1],
            self._tls_version, self._cipher,
        )

    def _handle_submit_answer(self, msg: dict) -> None:
        """Process an answer submission."""
        if not self.username:
            self.send_message({"type": "error", "message": "join_first"})
            return

        answer = str(msg.get("answer", "")).strip()
        client_sent_ts = msg.get("client_sent_ts")
        if client_sent_ts is not None:
            try:
                client_sent_ts = float(client_sent_ts)
            except (TypeError, ValueError):
                client_sent_ts = None

        result = self.session.submit_answer(self.username, answer, client_sent_ts)
        self.send_message({"type": "answer_result", **result})

    def _handle_start_quiz(self) -> None:
        """Request the server to start the quiz."""
        if not self.username:
            self.send_message({"type": "error", "message": "join_first"})
            return

        ok, reason = self.session.start_quiz(self.username)
        if ok:
            self.send_message({"type": "quiz_started", "message": reason})
        else:
            self.send_message({"type": "start_rejected", "message": reason})

    # ── Cleanup ───────────────────────────────────────────────

    def stop(self) -> None:
        """Signal the handler to shut down gracefully."""
        self._running = False

    def _cleanup(self) -> None:
        """Disconnect and notify the server."""
        self._running = False
        if self.username:
            self.session.mark_disconnected(self.username)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        self._on_disconnect(self)
        logger.info("Handler for '%s' cleaned up.", self.username or self.address)
