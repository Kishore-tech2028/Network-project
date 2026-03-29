"""
web_server.py — HTTPS & WebSocket Server for Quiz Frontend
─────────────────────────────────────────────────────────
Serves static frontend files over HTTPS and acts as a
WebSocket-to-SessionManager bridge for real-time quiz logic.

Usage:
    python web_server.py
"""

import base64
import hashlib
import json
import logging
import socket
import ssl
import struct
import sys
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# Ensure Server/ is on sys.path so local imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))

from session_manager import SessionManager
import config

logging.basicConfig(
    level=config.LOG_LEVEL,
    format=config.LOG_FORMAT,
    datefmt=config.LOG_DATE_FORMAT,
)
logger = logging.getLogger("quiz.webserver")


# ── WebSocket Helper ──────────────────────────────────────────

class WebSocketFrame:
    """Helper to parse and build RFC 6455 WebSocket frames."""
    
    @staticmethod
    def encode(payload: str) -> bytes:
        payload_bytes = payload.encode("utf-8")
        length = len(payload_bytes)
        frame = bytearray([0x81]) # FIN + Text Frame

        if length <= 125:
            frame.append(length)
        elif length >= 126 and length <= 65535:
            frame.append(126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(127)
            frame.extend(struct.pack(">Q", length))
        
        frame.extend(payload_bytes)
        return bytes(frame)

    @staticmethod
    def _recv_exactly(sock: socket.socket, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            packet = sock.recv(n - len(data))
            if not packet:
                return b""
            data.extend(packet)
        return bytes(data)

    @staticmethod
    def decode(sock: socket.socket) -> str | None:
        try:
            head = WebSocketFrame._recv_exactly(sock, 2)
            if not head or len(head) != 2:
                return None
            
            b1, b2 = head
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            payload_len = b2 & 0x7F

            if opcode == 0x8: # Close frame
                return None
            
            # PING or PONG frames
            if opcode in (0x9, 0xA):
                # Drain the payload but ignore it (we don't strictly echo PONGs here, just keep conn alive)
                if payload_len == 126:
                    ext_len = WebSocketFrame._recv_exactly(sock, 2)
                    payload_len = struct.unpack(">H", ext_len)[0]
                elif payload_len == 127:
                    ext_len = WebSocketFrame._recv_exactly(sock, 8)
                    payload_len = struct.unpack(">Q", ext_len)[0]
                
                masks = WebSocketFrame._recv_exactly(sock, 4) if masked else b""
                WebSocketFrame._recv_exactly(sock, payload_len) # consume
                return '{"action":"ping"}' # return a safe dummy ping message

            if payload_len == 126:
                ext_len = WebSocketFrame._recv_exactly(sock, 2)
                payload_len = struct.unpack(">H", ext_len)[0]
            elif payload_len == 127:
                ext_len = WebSocketFrame._recv_exactly(sock, 8)
                payload_len = struct.unpack(">Q", ext_len)[0]
            
            masks = WebSocketFrame._recv_exactly(sock, 4) if masked else b""
            payload = WebSocketFrame._recv_exactly(sock, payload_len)

            if masked:
                unmasked = bytearray(payload_len)
                for i in range(payload_len):
                    unmasked[i] = payload[i] ^ masks[i % 4]
                payload = bytes(unmasked)
            
            return payload.decode("utf-8")
        except Exception as e:
            logger.debug("WebSocket decode error (likely disconnect): %s", e)
            return None


# ── WebSocket Client Bridge ───────────────────────────────────

class WebClientBridge:
    def __init__(self, sock: socket.socket, address: tuple, session: SessionManager, server: "QuizWebServer"):
        self.sock = sock
        self.address = address
        self.session = session
        self.server = server
        self.username = None
        self._send_lock = threading.Lock()
        self._running = True

    def send_message(self, msg: dict) -> bool:
        with self._send_lock:
            try:
                data = json.dumps(msg)
                frame = WebSocketFrame.encode(data)
                self.sock.sendall(frame)
                return True
            except OSError:
                return False

    def start(self):
        # We allow the socket to block indefinitely. ThreadingHTTPServer handles
        # threads and connections closing will return `b""` from recv.
        self.sock.setblocking(True)
        try:
            while self._running:
                raw = WebSocketFrame.decode(self.sock)
                if raw is None:
                    break
                try:
                    msg = json.loads(raw)
                    self._dispatch(msg.get("action", ""), msg)
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logger.error("Client error: %s", e)
        finally:
            self.stop()

    def stop(self):
        self._running = False
        if self.username:
            self.session.mark_disconnected(self.username)
        try:
            self.sock.close()
        except OSError:
            pass
        self.server._on_client_disconnect(self)

    def _dispatch(self, action: str, msg: dict):
        if action == "join":
            requested_name = str(msg.get("username", "anonymous")).strip()[:32]
            self.username = requested_name if requested_name else "anonymous"
            is_host = bool(msg.get("host", False))
            status = self.session.add_participant(self.username, is_host)
            state = self.session.get_state_snapshot()
            
            self.send_message({
                "type": "welcome",
                "username": self.username,
                "status": status,
                "role": "host" if state.get("host") == self.username else "player",
                "is_host": state.get("host") == self.username,
                "host": state.get("host"),
                "participants": self.session.get_connected_count(),
                "participant_list": state.get("participants", []),
                "quiz_started": state.get("quiz_started", False),
                "quiz_finished": state.get("quiz_finished", False),
                "quiz_start_ts": state.get("quiz_start_ts", 0.0),
                "current_question": state.get("current_question", {}),
                "requires_ready": bool(
                    self.username != state.get("host")
                    and state.get("quiz_started", False)
                    and not state.get("quiz_finished", False)
                    and not self.session.can_receive_questions(self.username)
                ),
                "ready_reason": "quiz_in_progress",
            })
            self.server.broadcast_participants_update()
            logger.info("Player '%s' joined via Web", self.username)
        
        elif action == "submit_answer":
            if not self.username: return
            if self.username == self.session.host:
                self.send_message({"type": "answer_result", "accepted": False, "reason": "host_monitor_only"})
                return
            answer = str(msg.get("answer", "")).strip()
            result = self.session.submit_answer(self.username, answer, msg.get("client_sent_ts"))
            self.send_message({"type": "answer_result", **result})
        
        elif action == "start_quiz":
            if not self.username: return
            ok, reason = self.session.start_quiz(self.username)
            if ok:
                self.server._broadcast({
                    "type": "quiz_countdown", 
                    "quiz_start_ts": self.session.quiz_start_ts
                })
                self.server.broadcast_participants_update()
            else:
                self.send_message({"type": "start_rejected", "message": reason})

        elif action == "restart_quiz":
            if not self.username:
                return
            ok, reason = self.session.restart_quiz(self.username)
            if ok:
                self.server._broadcast({"type": "quiz_reset"})
                self.server._broadcast({
                    "type": "quiz_countdown",
                    "quiz_start_ts": self.session.quiz_start_ts,
                })
                self.server.broadcast_participants_update()
            else:
                self.send_message({"type": "restart_rejected", "message": reason})

        elif action == "set_ready":
            if not self.username:
                return
            ready = bool(msg.get("ready", True))
            ok, reason = self.session.set_participant_ready(self.username, ready=ready)
            if ok:
                self.send_message({"type": "ready_updated", "ready": ready, "message": reason})
                self.server.broadcast_participants_update()
            else:
                self.send_message({"type": "ready_rejected", "message": reason})

        elif action == "leave_quiz":
            if self.username:
                if self.username == self.session.host:
                    self.send_message({"type": "ready_rejected", "message": "host_cannot_leave"})
                    return
                self.session.remove_participant(self.username)
            self.send_message({"type": "left_quiz"})
            self.stop()


# ── HTTP/WebSocket Handler ────────────────────────────────────

class QuizHTTPRequestHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "QuizWebServer" # Type hint

    def __init__(self, *args, **kwargs):
        # Serve files from frontend/
        base_dir = Path(__file__).resolve().parent / "frontend"
        if not base_dir.exists():
            base_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(*args, directory=str(base_dir), **kwargs)

    def handle_one_request(self):
        """Override to log TLS connection details."""
        try:
            # Log successful TLS/SSL connection
            peer_ip = self.client_address[0]
            peer_port = self.client_address[1]
            
            # Check if connection is over TLS
            try:
                cipher = self.connection.getpeername()
                # Try to get SSL info if available
                if hasattr(self.connection, 'getpeercert'):
                    logger.info("✓ TLS connection established [%s:%d] using %s", 
                               peer_ip, peer_port, config.TLS_PROTOCOL_VERSION)
                else:
                    logger.debug("Client connection from [%s:%d]", peer_ip, peer_port)
            except (AttributeError, OSError):
                logger.debug("Client connection from [%s:%d]", peer_ip, peer_port)
            
            super().handle_one_request()
        except Exception as e:
            logger.error("Connection error from [%s:%d]: %s", 
                        self.client_address[0], self.client_address[1], e)

    def do_GET(self):
        if self.path == "/ws":
            self._handle_websocket_upgrade()
        else:
            # Fallback to index.html for root or unknown routes
            if self.path == "/":
                self.path = "/index.html"
            super().do_GET()

    def _handle_websocket_upgrade(self):
        # Ensure it's a websocket upgrade request
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "Missing WebSocket Key")
            return

        # Accept handshake
        guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        accept_key = base64.b64encode(hashlib.sha1((key + guid).encode("utf-8")).digest()).decode("utf-8")

        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept_key)
        self.end_headers()
        self.close_connection = True

        # Log WebSocket upgrade
        peer_ip = self.client_address[0]
        peer_port = self.client_address[1]
        logger.info("⬆ WebSocket upgrade from [%s:%d] → /ws (RFC 6455)", peer_ip, peer_port)

        # Hijack connection from HTTPServer
        self.connection.setblocking(True)
        client = WebClientBridge(self.connection, self.client_address, self.server.session, self.server)
        
        with self.server._clients_lock:
            self.server.clients.append(client)
        
        # Run bridge until it exits
        client.start()


# ── Server Coordinator ────────────────────────────────────────

class QuizWebServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, session, bind_and_activate=True):
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        self.session = session
        self.clients: list[WebClientBridge] = []
        self._clients_lock = threading.Lock()
        self._timer_thread = None
        self._running = False

    def _on_client_disconnect(self, client: WebClientBridge):
        with self._clients_lock:
            if client in self.clients:
                self.clients.remove(client)
        self._broadcast({"type": "leaderboard", "rankings": self.session.get_leaderboard()})
        self.broadcast_participants_update()

    def _build_participants_update(self) -> dict:
        state = self.session.get_state_snapshot()
        participants = state.get("participants", [])
        host_name = state.get("host")
        connected_count = sum(
            1
            for participant in participants
            if participant.get("connected") and participant.get("name") != host_name
        )
        return {
            "type": "participants_update",
            "host": state.get("host"),
            "participants": participants,
            "connected_count": connected_count,
            "total_count": len(participants),
            "quiz_started": state.get("quiz_started", False),
            "quiz_finished": state.get("quiz_finished", False),
            "quiz_start_ts": state.get("quiz_start_ts", 0.0),
            "current_question": state.get("current_question", {}),
        }

    def broadcast_participants_update(self):
        self._broadcast(self._build_participants_update())

    def _broadcast(self, msg: dict):
        with self._clients_lock:
            for client in list(self.clients):
                client.send_message(msg)

    def _broadcast_to_players(self, msg: dict):
        with self._clients_lock:
            for client in list(self.clients):
                if not client.username:
                    continue
                if not self.session.can_receive_questions(client.username):
                    continue
                client.send_message(msg)

    def serve_forever(self, poll_interval=0.5):
        self._running = True
        # Start background timer thread
        self._timer_thread = threading.Thread(target=self._round_timer_poll, daemon=True)
        self._timer_thread.start()
        super().serve_forever(poll_interval)

    def shutdown(self):
        self._running = False
        with self._clients_lock:
            for client in list(self.clients):
                client.stop()
        super().shutdown()

    def _round_timer_poll(self):
        """Monitors SessionManager and broadcasts question rounds."""
        while self._running:
            if not self.session.started or self.session.finished:
                time.sleep(0.5)
                continue

            # 1. Countdown Phase Handler
            if self.session.current_question_index == -1:
                sleep_time = max(0.0, self.session.quiz_start_ts - time.time())
                time.sleep(sleep_time)
                if not self.session.finished:
                    self.session.advance_to_next_question()
                continue

            question = self.session.get_current_question()
            if question:
                self._broadcast_to_players({"type": "question", "payload": question})
                self.broadcast_participants_update()

            # Wait until question deadline
            sleep_time = max(0.0, self.session.question_deadline_ts - time.time())
            time.sleep(sleep_time)

            if self.session.finished:
                continue

            correct = self.session.get_correct_answer()
            self._broadcast_to_players({
                "type": "question_closed",
                "payload": {
                    "index": self.session.current_question_index,
                    "answer": correct,
                    "leaderboard": self.session.get_leaderboard(),
                },
            })
            self.broadcast_participants_update()

            time.sleep(self.session.TRANSITION_SECONDS)

            next_state = self.session.advance_to_next_question()
            if next_state.get("finished"):
                self._broadcast({
                    "type": "quiz_finished",
                    "leaderboard": self.session.get_leaderboard(),
                })
                self.broadcast_participants_update()
                logger.info("Quiz finished! Final leaderboard broadcast.")


def main():
    import argparse
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description="Quiz Server — HTTPS & WebSocket listener for real-time quiz delivery"
    )
    parser.add_argument("--host", default=config.DEFAULT_HOST, 
                       help=f"Bind address (default: {config.DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT, 
                       help=f"HTTPS Port (default: {config.DEFAULT_PORT})")
    parser.add_argument("--cert", default=str(script_dir / config.DEFAULT_CERT_FILE),
                       help=f"SSL Certificate file (default: {config.DEFAULT_CERT_FILE})")
    parser.add_argument("--key", default=str(script_dir / config.DEFAULT_KEY_FILE),
                       help=f"SSL Key file (default: {config.DEFAULT_KEY_FILE})")
    parser.add_argument("--questions", default=str(script_dir / config.DEFAULT_QUESTIONS_FILE),
                       help=f"Questions JSON file (default: {config.DEFAULT_QUESTIONS_FILE})")
    args = parser.parse_args()

    # Init Quiz Session Manager
    session = SessionManager(args.questions)

    # Init HTTPS Server
    server = QuizWebServer((args.host, args.port), QuizHTTPRequestHandler, session)
    
    # Wrap with SSL Context
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=args.cert, keyfile=args.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)

    # Log server startup with TLS details
    logger.info("=" * 60)
    logger.info("🔒 TLS/SSL Server Configuration")
    logger.info("   Protocol:           %s", config.TLS_PROTOCOL_VERSION)
    logger.info("   Certificate:        %s", args.cert)
    logger.info("   Private Key:        %s", args.key)
    logger.info("=" * 60)
    logger.info("✓ Quiz Server (Web) listening on https://%s:%d", args.host, args.port)
    logger.info("✓ WebSockets active on wss://%s:%d/ws", args.host, args.port)
    logger.info("✓ Questions loaded: %d", len(session.questions))
    logger.info("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
    finally:
        server.shutdown()
        logger.info("Server stopped.")

if __name__ == "__main__":
    main()
