"""
quiz_socket_client.py — CLI client for the TLS Quiz Server
───────────────────────────────────────────────────────────
Connects to the quiz server over TLS (import ssl), joins
as a participant, receives questions, submits answers, and
displays the real-time leaderboard.

Usage:
    python Simple_Server/quiz_socket_client.py --username Alice
    python Simple_Server/quiz_socket_client.py --username Bob --host 127.0.0.1 --port 12345
"""

import argparse
import json
import logging
import socket
import ssl
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("quiz.client")


# ── Client class ──────────────────────────────────────────────

class QuizClient:
    """
    TLS quiz client.

    • Connects to the server using ssl.SSLContext with the self-signed CA cert
    • Sends join, submit_answer, start_quiz, ping actions
    • Receives and displays questions, leaderboard, results
    • Logs TLS handshake info (cipher, version)
    """

    RECV_BUFFER = 4096

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 12345,
        username: str = "Player",
        ca_cert: str = "server.crt",
        is_host: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.ca_cert = ca_cert
        self.is_host = is_host

        self._sock: ssl.SSLSocket | None = None
        self._running = False
        self._recv_buffer = ""

    # ── SSL Context ───────────────────────────────────────────

    def _create_ssl_context(self) -> ssl.SSLContext:
        """
        Build an SSL context for the client.
        Loads the server's self-signed certificate as a trusted CA.
        Uses only `import ssl` — no third-party TLS libraries.
        """
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=self.ca_cert)

        # For self-signed certs: trust the server cert directly
        # Disable hostname check since it's a self-signed cert for localhost
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED

        logger.info("SSL context created (client mode):")
        logger.info("  CA Certificate: %s", self.ca_cert)
        logger.info("  Verify Mode   : CERT_REQUIRED")
        return context

    # ── Connection ────────────────────────────────────────────

    def connect(self) -> None:
        """Establish a TLS connection to the quiz server."""
        ssl_ctx = self._create_ssl_context()

        raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock = ssl_ctx.wrap_socket(raw_socket, server_hostname=self.host)
        self._sock.connect((self.host, self.port))
        self._running = True

        # Log TLS handshake details
        cipher = self._sock.cipher()
        tls_version = self._sock.version()
        logger.info("═" * 56)
        logger.info("  Connected to %s:%d (TLS)", self.host, self.port)
        logger.info("  TLS Version : %s", tls_version)
        logger.info(
            "  Cipher Suite: %s (bits=%s)",
            cipher[0] if cipher else "N/A",
            cipher[2] if cipher else "N/A",
        )
        server_cert = self._sock.getpeercert()
        if server_cert:
            subject = dict(x[0] for x in server_cert.get("subject", ()))
            logger.info("  Server CN   : %s", subject.get("commonName", "N/A"))
            logger.info("  Valid Until  : %s", server_cert.get("notAfter", "N/A"))
        logger.info("═" * 56)

    # ── Network I/O ───────────────────────────────────────────

    def send_message(self, msg: dict) -> None:
        """Send a JSON message (newline-delimited) over TLS."""
        if self._sock:
            data = json.dumps(msg) + "\n"
            self._sock.sendall(data.encode("utf-8"))

    def _recv_messages(self) -> None:
        """Background thread: receive and handle server messages."""
        while self._running:
            try:
                chunk = self._sock.recv(self.RECV_BUFFER)
                if not chunk:
                    logger.info("Server closed connection.")
                    self._running = False
                    return
                self._recv_buffer += chunk.decode("utf-8")

                # Process complete messages (newline-delimited)
                while "\n" in self._recv_buffer:
                    line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            self._handle_server_message(msg)
                        except json.JSONDecodeError:
                            logger.warning("Invalid JSON from server: %s", line[:100])
            except socket.timeout:
                continue
            except (OSError, ssl.SSLError) as exc:
                if self._running:
                    logger.error("Connection error: %s", exc)
                self._running = False
                return

    # ── Message handling ──────────────────────────────────────

    def _handle_server_message(self, msg: dict) -> None:
        """Process a message received from the server."""
        msg_type = msg.get("type", "")

        if msg_type == "welcome":
            self._show_welcome(msg)
        elif msg_type == "question":
            self._show_question(msg.get("payload", {}))
        elif msg_type == "answer_result":
            self._show_answer_result(msg)
        elif msg_type == "leaderboard":
            self._show_leaderboard(msg.get("rankings", []))
        elif msg_type == "question_closed":
            self._show_question_closed(msg.get("payload", {}))
        elif msg_type == "quiz_started":
            print("\n🚀 Quiz has started!")
        elif msg_type == "quiz_finished":
            self._show_quiz_finished(msg)
        elif msg_type == "start_rejected":
            print(f"\n❌ Cannot start: {msg.get('message', '')}")
        elif msg_type == "pong":
            rtt = (time.time() - msg.get("server_ts", time.time())) * 1000
            print(f"  ↩ Pong (server_ts delay: {abs(rtt):.1f}ms)")
        elif msg_type == "error":
            print(f"\n⚠ Server error: {msg.get('message', '')}")
        else:
            logger.debug("Unknown message type: %s", msg_type)

    def _show_welcome(self, msg: dict) -> None:
        print("\n╔══════════════════════════════════════════╗")
        print(f"║  Welcome, {msg.get('username', '?'):<30s} ║")
        print(f"║  Status: {msg.get('status', ''):<31s} ║")
        print(f"║  Host  : {msg.get('host', '?'):<31s} ║")
        print(f"║  TLS   : {msg.get('tls_version', '?'):<31s} ║")
        print(f"║  Cipher: {msg.get('cipher', '?'):<31s} ║")
        print(f"║  Players online: {msg.get('participants', 0):<23} ║")
        print("╚══════════════════════════════════════════╝")
        if msg.get("quiz_started"):
            print("  ℹ Quiz is already in progress.")
        elif msg.get("host") == msg.get("username"):
            print("  ★ You are the HOST. Type 'start' to begin the quiz.")
        else:
            print("  ⏳ Waiting for the host to start the quiz...")

    def _show_question(self, payload: dict) -> None:
        if not payload:
            return
        idx = payload.get("index", 0)
        total = payload.get("total_questions", "?")
        duration = payload.get("duration", 10)
        print(f"\n{'─' * 50}")
        print(f"  Question {idx + 1}/{total}  (⏱ {duration}s)")
        print(f"  {payload.get('question', '')}")
        print(f"{'─' * 50}")
        options = payload.get("options", [])
        for i, opt in enumerate(options, 1):
            print(f"    {i}. {opt}")
        print(f"\n  Type the answer or option number (1-{len(options)}):")

    def _show_answer_result(self, msg: dict) -> None:
        if msg.get("accepted"):
            correct_str = "✅ Correct!" if msg.get("correct") else "❌ Wrong"
            print(f"\n  {correct_str}  Score: {msg.get('score', 0)}  "
                  f"Latency: {msg.get('latency_ms', 0):.1f}ms  "
                  f"Fairness: +{msg.get('fairness_allowance_s', 0):.3f}s")
        else:
            print(f"\n  ⛔ Answer rejected: {msg.get('reason', 'unknown')}")

    def _show_leaderboard(self, rankings: list) -> None:
        if not rankings:
            return
        print("\n  📊 Leaderboard:")
        for entry in rankings:
            status = "🟢" if entry.get("connected") else "🔴"
            print(f"    {status} #{entry.get('rank', '?')} {entry['name']:<15s} "
                  f"Score: {entry.get('score', 0)}  "
                  f"Latency: {entry.get('latency_ms', 0):.1f}ms")

    def _show_question_closed(self, payload: dict) -> None:
        answer = payload.get("answer", "?")
        print(f"\n  ⏰ Time's up! Correct answer: {answer}")

    def _show_quiz_finished(self, msg: dict) -> None:
        print("\n╔══════════════════════════════════════════╗")
        print("║            🏆 QUIZ FINISHED! 🏆          ║")
        print("╠══════════════════════════════════════════╣")
        rankings = msg.get("leaderboard", [])
        for entry in rankings:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(entry.get("rank", 99), "  ")
            print(f"║  {medal} {entry['name']:<15s} Score: {entry.get('score', 0):<8} ║")
        print("╚══════════════════════════════════════════╝")

    # ── Interactive loop ──────────────────────────────────────

    def run_interactive(self) -> None:
        """Join the quiz and start the interactive command loop."""
        self.connect()

        # Join the quiz
        self.send_message({
            "action": "join",
            "username": self.username,
            "host": self.is_host,
        })

        # Start receiver thread
        self._sock.settimeout(1.0)
        recv_thread = threading.Thread(target=self._recv_messages, daemon=True)
        recv_thread.start()

        # Interactive input
        print("\nCommands: type an answer, 'start', 'ping', or 'quit'")
        try:
            while self._running:
                try:
                    user_input = input("> ").strip()
                except EOFError:
                    break

                if not user_input:
                    continue

                if user_input.lower() == "quit":
                    break
                elif user_input.lower() == "start":
                    self.send_message({"action": "start_quiz"})
                elif user_input.lower() == "ping":
                    self.send_message({"action": "ping"})
                else:
                    self.send_message({
                        "action": "submit_answer",
                        "answer": user_input,
                        "client_sent_ts": time.time(),
                    })
        except KeyboardInterrupt:
            print("\nDisconnecting...")
        finally:
            self._running = False
            if self._sock:
                try:
                    self._sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self._sock.close()
            print("Disconnected. Goodbye!")


# ── CLI entry point ───────────────────────────────────────────

def main() -> None:
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Quiz Socket Client — TLS quiz participant")
    parser.add_argument("--host", default="127.0.0.1", help="Server address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=12345, help="Server port (default: 12345)")
    parser.add_argument("--username", default="Player", help="Your display name")
    parser.add_argument(
        "--ca-cert", default=str(script_dir / "server.crt"),
        help="Path to server's CA certificate (default: Simple_Server/server.crt)",
    )
    parser.add_argument("--host-mode", action="store_true", help="Join as quiz host")
    args = parser.parse_args()

    if not Path(args.ca_cert).exists():
        logger.error("CA certificate not found: %s", args.ca_cert)
        logger.error("Run:  bash Simple_Server/generate_cert.sh")
        sys.exit(1)

    client = QuizClient(
        host=args.host,
        port=args.port,
        username=args.username,
        ca_cert=args.ca_cert,
        is_host=args.host_mode,
    )
    client.run_interactive()


if __name__ == "__main__":
    main()
