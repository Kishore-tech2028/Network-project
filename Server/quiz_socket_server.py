"""
quiz_socket_server.py — Main TLS Quiz Server
─────────────────────────────────────────────
Binds a TCP socket, wraps it with ssl.SSLContext using
a self-signed certificate, accepts concurrent clients,
and coordinates quiz rounds via SessionManager.

Usage:
    python Simple_Server/quiz_socket_server.py
    python Simple_Server/quiz_socket_server.py --host 0.0.0.0 --port 12345
"""

import argparse
import json
import logging
import os
import socket
import ssl
import sys
import threading
import time
from pathlib import Path

# Ensure Simple_Server/ is on sys.path so local imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_manager import SessionManager
from client_handler import ClientHandler

# ── Logging setup ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("quiz.server")


# ── Server class ──────────────────────────────────────────────

class QuizSocketServer:
    """
    Multi-client TLS quiz server.

    • Creates a TCP socket wrapped with ssl.SSLContext
    • Accepts concurrent clients via threading
    • Spawns a ClientHandler per client
    • Runs a timer thread for round progression
    • Broadcasts questions / leaderboard to all clients
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 12345,
        certfile: str = "server.crt",
        keyfile: str = "server.key",
        questions_path: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile

        # Resolve default questions path
        if not questions_path:
            base = Path(__file__).resolve().parent
            questions_path = str(
                base / "questions.json"
            )

        # Session manager (thread-safe quiz logic)
        self.session = SessionManager(questions_path)

        # Client tracking
        self._clients: list[ClientHandler] = []
        self._clients_lock = threading.Lock()

        # Server socket
        self._server_socket: socket.socket | None = None
        self._running = False

        # Round timer
        self._timer_thread: threading.Thread | None = None

    # ── SSL Context ───────────────────────────────────────────

    def _create_ssl_context(self) -> ssl.SSLContext:
        """
        Build an SSL context for the server using the self-signed cert.
        Uses only `import ssl` — no third-party TLS libraries.
        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            certfile=self.certfile,
            keyfile=self.keyfile,
        )
        # Modern TLS settings
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("HIGH:!aNULL:!MD5:!RC4")

        logger.info("SSL context created:")
        logger.info("  Certificate : %s", self.certfile)
        logger.info("  Private Key : %s", self.keyfile)
        logger.info("  Min TLS     : TLSv1.2")
        logger.info("  Ciphers     : HIGH:!aNULL:!MD5:!RC4")
        return context

    # ── Server lifecycle ──────────────────────────────────────

    def start(self) -> None:
        """Bind, listen, and accept TLS client connections."""
        ssl_ctx = self._create_ssl_context()

        raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw_socket.bind((self.host, self.port))
        raw_socket.listen(10)

        self._server_socket = ssl_ctx.wrap_socket(raw_socket, server_side=True)
        self._running = True

        logger.info("═" * 56)
        logger.info("  Quiz Server listening on %s:%d (TLS)", self.host, self.port)
        logger.info("  Questions loaded: %d", len(self.session.questions))
        logger.info("  Waiting for players to connect...")
        logger.info("═" * 56)

        try:
            while self._running:
                try:
                    client_sock, client_addr = self._server_socket.accept()
                    logger.info(
                        "New TLS connection from %s:%d", client_addr[0], client_addr[1]
                    )
                    handler = ClientHandler(
                        client_socket=client_sock,
                        client_address=client_addr,
                        session=self.session,
                        on_disconnect=self._on_client_disconnect,
                    )
                    with self._clients_lock:
                        self._clients.append(handler)
                    handler.start()
                except ssl.SSLError as e:
                    logger.warning("SSL handshake failed: %s", e)
                except OSError:
                    if self._running:
                        raise
        except KeyboardInterrupt:
            logger.info("Server shutting down (Ctrl+C)...")
        finally:
            self.stop()

    def stop(self) -> None:
        """Gracefully shut down the server and all clients."""
        self._running = False
        with self._clients_lock:
            for handler in self._clients:
                handler.stop()
        if self._server_socket:
            try:
                self._server_socket.close()
            except OSError:
                pass
        logger.info("Server stopped.")

    # ── Client management ─────────────────────────────────────

    def _on_client_disconnect(self, handler: ClientHandler) -> None:
        """Callback when a client handler finishes."""
        with self._clients_lock:
            if handler in self._clients:
                self._clients.remove(handler)
        # Broadcast updated leaderboard
        self._broadcast({"type": "leaderboard", "rankings": self.session.get_leaderboard()})
        logger.info(
            "Active connections: %d  |  Players: %d",
            len(self._clients),
            self.session.get_connected_count(),
        )

    def _broadcast(self, msg: dict) -> None:
        """Send a message to all connected clients."""
        with self._clients_lock:
            for handler in list(self._clients):
                handler.send_message(msg)

    # ── Round timer (quiz progression) ────────────────────────

    def start_quiz_rounds(self) -> None:
        """
        Called after the quiz is started.
        Runs in a background thread to manage question timing.
        """
        if self._timer_thread and self._timer_thread.is_alive():
            return
        self._timer_thread = threading.Thread(target=self._round_timer_loop, daemon=True)
        self._timer_thread.start()

    def _round_timer_loop(self) -> None:
        """Timer thread: waits for deadline, reveals answer, advances."""
        while self._running:
            if self.session.finished or not self.session.started:
                return

            # Send current question to all clients
            question = self.session.get_current_question()
            if question:
                self._broadcast({"type": "question", "payload": question})

            # Wait until question deadline
            sleep_time = max(0.0, self.session.question_deadline_ts - time.time())
            time.sleep(sleep_time)

            if self.session.finished or not self.session.started:
                return

            # Reveal correct answer
            correct = self.session.get_correct_answer()
            self._broadcast({
                "type": "question_closed",
                "payload": {
                    "index": self.session.current_question_index,
                    "answer": correct,
                    "leaderboard": self.session.get_leaderboard(),
                },
            })

            # Transition pause
            time.sleep(self.session.TRANSITION_SECONDS)

            # Advance to next question
            next_state = self.session.advance_to_next_question()
            if next_state.get("finished"):
                self._broadcast({
                    "type": "quiz_finished",
                    "leaderboard": self.session.get_leaderboard(),
                })
                logger.info("Quiz finished! Final leaderboard broadcast.")
                return

    # ── Polling thread for quiz start ─────────────────────────

    def _poll_for_quiz_start(self) -> None:
        """Background thread: waits for quiz to start, then runs rounds."""
        while self._running and not self.session.started:
            time.sleep(0.5)
        if self.session.started:
            self.start_quiz_rounds()

    def run_with_start_detection(self) -> None:
        """Start the server with automatic quiz-start detection."""
        poll_thread = threading.Thread(target=self._poll_for_quiz_start, daemon=True)
        poll_thread.start()
        self.start()


# ── CLI entry point ───────────────────────────────────────────

def main() -> None:
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description="Quiz Socket Server — Multi-client TLS quiz platform",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=12345, help="Port (default: 12345)")
    parser.add_argument(
        "--cert", default=str(script_dir / "server.crt"),
        help="Path to TLS certificate (default: Simple_Server/server.crt)",
    )
    parser.add_argument(
        "--key", default=str(script_dir / "server.key"),
        help="Path to TLS private key (default: Simple_Server/server.key)",
    )
    parser.add_argument("--questions", default="", help="Path to questions.json")
    args = parser.parse_args()

    # Verify cert files exist
    if not Path(args.cert).exists():
        logger.error("Certificate not found: %s", args.cert)
        logger.error("Run:  bash Simple_Server/generate_cert.sh")
        sys.exit(1)
    if not Path(args.key).exists():
        logger.error("Private key not found: %s", args.key)
        sys.exit(1)

    server = QuizSocketServer(
        host=args.host,
        port=args.port,
        certfile=args.cert,
        keyfile=args.key,
        questions_path=args.questions,
    )
    server.run_with_start_detection()


if __name__ == "__main__":
    main()
