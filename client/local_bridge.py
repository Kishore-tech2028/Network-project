#!/usr/bin/env python3
"""
local_bridge.py — Browser-to-WSS Bridge
────────────────────────────────────────
Runs on each client laptop to forward browser WebSocket traffic
to the central quiz web server's WSS endpoint (/ws).

Usage:
    python local_bridge.py --server-host <SERVER_IP>
"""

import argparse
import base64
import hashlib
import logging
import os
import socket
import ssl
import struct
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import cast

import config

logging.basicConfig(
    level=config.LOG_LEVEL,
    format=config.LOG_FORMAT,
    datefmt=config.LOG_DATE_FORMAT,
)
logger = logging.getLogger("quiz.local_bridge")

class WebSocketFrame:
    @staticmethod
    def encode(payload: str, masked: bool = False) -> bytes:
        payload_bytes = payload.encode("utf-8")
        length = len(payload_bytes)
        frame = bytearray([0x81])

        mask_bit = 0x80 if masked else 0x00
        if length <= 125:
            frame.append(mask_bit | length)
        elif length <= 65535:
            frame.append(mask_bit | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(mask_bit | 127)
            frame.extend(struct.pack(">Q", length))

        if masked:
            mask_key = os.urandom(4)
            frame.extend(mask_key)
            masked_payload = bytearray(length)
            for i in range(length):
                masked_payload[i] = payload_bytes[i] ^ mask_key[i % 4]
            frame.extend(masked_payload)
            return bytes(frame)

        frame.extend(payload_bytes)
        return bytes(frame)

    @staticmethod
    def _recv_exactly(sock_obj: socket.socket, n: int) -> bytes:
        data = bytearray()
        while len(data) < n:
            packet = sock_obj.recv(n - len(data))
            if not packet: return b""
            data.extend(packet)
        return bytes(data)

    @staticmethod
    def decode(sock_obj: socket.socket) -> str | None:
        try:
            head = WebSocketFrame._recv_exactly(sock_obj, 2)
            if not head or len(head) != 2:
                return None
            b1, b2 = head
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            payload_len = b2 & 0x7F
            if opcode == 0x8:
                return None
            if payload_len == 126:
                ext_len = WebSocketFrame._recv_exactly(sock_obj, 2)
                payload_len = struct.unpack(">H", ext_len)[0]
            elif payload_len == 127:
                ext_len = WebSocketFrame._recv_exactly(sock_obj, 8)
                payload_len = struct.unpack(">Q", ext_len)[0]
            masks = WebSocketFrame._recv_exactly(sock_obj, 4) if masked else b""
            payload = WebSocketFrame._recv_exactly(sock_obj, payload_len)
            if payload_len and not payload:
                return None
            if masked:
                unmasked = bytearray(payload_len)
                for i in range(payload_len):
                    unmasked[i] = payload[i] ^ masks[i % 4]
                payload = bytes(unmasked)

            # Ignore control frames in bridge mode (already drained above)
            if opcode in (0x9, 0xA):
                return ""

            return payload.decode("utf-8")
        except Exception:
            return None

class BridgeConnection:
    def __init__(self, browser_sock, server_host, server_port, ca_cert, server_name, ws_path="/ws"):
        self.browser_sock = browser_sock
        self.server_host = server_host
        self.server_port = server_port
        self.ca_cert = ca_cert
        self.server_name = server_name
        self.ws_path = ws_path
        self.remote_sock: Optional[ssl.SSLSocket] = None
        self.running = True

    def stop(self):
        self.running = False
        for sock_obj in (self.remote_sock, self.browser_sock):
            if not sock_obj:
                continue
            try:
                sock_obj.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock_obj.close()
            except OSError:
                pass

    def start(self):
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.load_verify_locations(cafile=self.ca_cert)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED
            raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.remote_sock = ctx.wrap_socket(raw, server_hostname=self.server_name)
            
            logger.info("↻ Connecting to upstream server [%s:%d]...", self.server_host, self.server_port)
            self.remote_sock.connect((self.server_host, self.server_port))
            
            # Log successful TLS connection
            logger.info("✓ TLS/SSL connection established with [%s:%d] using %s", 
                       self.server_host, self.server_port, config.TLS_PROTOCOL_VERSION)
            
            self._perform_upstream_websocket_handshake()
            logger.info("⬆ WebSocket upgrade successful (RFC 6455) → wss://%s:%d%s", 
                       self.server_host, self.server_port, self.ws_path)
            
            threading.Thread(target=self._browser_to_remote, daemon=True).start()
            self._remote_to_browser()
        except ssl.SSLError as e:
            logger.error("✗ TLS/SSL error during upstream connection: %s", e)
        except ConnectionError as e:
            logger.error("✗ Connection error: %s", e)
        except Exception as e:
            logger.error("✗ Bridge connection failed: %s", e)
        finally:
            self.stop()

    def _perform_upstream_websocket_handshake(self):
        if not self.remote_sock:
            raise RuntimeError("upstream socket not connected")

        sec_key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.ws_path} HTTP/1.1\r\n"
            f"Host: {self.server_host}:{self.server_port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {sec_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.remote_sock.sendall(request.encode("utf-8"))

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.remote_sock.recv(4096)
            if not chunk:
                raise ConnectionError("upstream closed during websocket handshake")
            response += chunk

        header_text = response.decode("utf-8", errors="replace")
        status_line = header_text.split("\r\n", 1)[0]
        if " 101 " not in status_line:
            raise ConnectionError(f"unexpected websocket handshake status: {status_line}")

        expected_accept = base64.b64encode(
            hashlib.sha1((sec_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")).digest()
        ).decode("ascii")
        if f"Sec-WebSocket-Accept: {expected_accept}".lower() not in header_text.lower():
            raise ConnectionError("invalid Sec-WebSocket-Accept from upstream")

    def _browser_to_remote(self):
        try:
            while self.running:
                raw = WebSocketFrame.decode(self.browser_sock)
                if raw is None:
                    break
                if raw == "":
                    continue
                if self.remote_sock:
                    self.remote_sock.sendall(WebSocketFrame.encode(raw, masked=True))
        except OSError:
            pass
        finally:
            self.running = False

    def _remote_to_browser(self):
        try:
            while self.running and self.remote_sock:
                message = WebSocketFrame.decode(self.remote_sock)
                if message is None:
                    break
                if message == "":
                    continue
                self.browser_sock.sendall(WebSocketFrame.encode(message))
        except OSError:
            pass
        finally:
            self.running = False

class BridgeHTTPRequestHandler(SimpleHTTPRequestHandler):
    frontend_dir = "."
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=self.frontend_dir, **kwargs)

    def log_message(self, format: str, *args) -> None:
        """Suppress default HTTP logging; we handle it explicitly."""
        return
    
    def do_GET(self):
        peer_ip = self.client_address[0]
        peer_port = self.client_address[1]
        
        if self.path == "/ws":
            logger.info("✓ TLS connection accepted from [%s:%d]", peer_ip, peer_port)
            
            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                self.send_error(400, "Missing Sec-WebSocket-Key")
                return

            accept_key = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key)
            self.end_headers()
            self.close_connection = True
            
            logger.info("⬆ WebSocket upgrade from browser [%s:%d] → /ws (RFC 6455)", peer_ip, peer_port)
            
            bridge_server = cast("BridgeHTTPServer", self.server)
            BridgeConnection(
                self.connection,
                bridge_server.quiz_host,
                bridge_server.quiz_port,
                bridge_server.ca_cert,
                bridge_server.quiz_host,
            ).start()
            return
        else:
            if self.path == "/": self.path = "/index.html"
            super().do_GET()

class BridgeHTTPServer(ThreadingHTTPServer):
    def __init__(self, addr, handler, quiz_host, quiz_port, ca_cert):
        super().__init__(addr, handler)
        self.quiz_host, self.quiz_port, self.ca_cert = quiz_host, quiz_port, ca_cert

def main():
    parser = argparse.ArgumentParser(
        description="Quiz Client Bridge — Local HTTPS/WebSocket proxy to central quiz server"
    )
    parser.add_argument("--quiz-host", 
                       help="[DEPRECATED] Use --server-host instead")
    parser.add_argument("--server-host", 
                       help="Central quiz server IP/hostname (required)")
    parser.add_argument("--quiz-port", type=int, 
                       help="[DEPRECATED] Use --server-port instead")
    parser.add_argument("--server-port", type=int, default=config.DEFAULT_SERVER_PORT,
                       help=f"Central quiz server port (default: {config.DEFAULT_SERVER_PORT})")
    parser.add_argument("--frontend", default="frontend",
                       help="Path to frontend directory to serve")
    parser.add_argument("--local-port", type=int, default=config.DEFAULT_LOCAL_PORT,
                       help=f"Local HTTPS port (default: {config.DEFAULT_LOCAL_PORT})")
    args = parser.parse_args()

    # Handle backward-compatible argument names
    upstream_host = args.server_host or args.quiz_host
    if not upstream_host:
        parser.error("error: one of --server-host or --quiz-host is required")

    upstream_port = args.quiz_port if args.quiz_port is not None else args.server_port

    script_dir = Path(__file__).resolve().parent
    local_cert = str(script_dir / config.DEFAULT_CERT_FILE)
    local_key = str(script_dir / config.DEFAULT_KEY_FILE)
    
    # Verify frontend directory exists
    frontend_path = Path(args.frontend)
    if not frontend_path.exists():
        logger.warning("⚠ Frontend directory not found: %s", args.frontend)
    
    # Set the frontend directory on the handler class
    BridgeHTTPRequestHandler.frontend_dir = args.frontend
    BridgeHTTPRequestHandler.protocol_version = "HTTP/1.1"
    server = BridgeHTTPServer((config.DEFAULT_LOCAL_HOST, args.local_port), BridgeHTTPRequestHandler, upstream_host, upstream_port, local_cert)
    
    # Wrap with TLS
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=local_cert, keyfile=local_key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    
    # Log startup with TLS details
    logger.info("=" * 70)
    logger.info("🔒 Quiz Client Bridge")
    logger.info("   Local HTTPS:        https://%s:%d", config.DEFAULT_LOCAL_HOST, args.local_port)
    logger.info("   Upstream WSS:       wss://%s:%d%s", upstream_host, upstream_port, config.UPSTREAM_WS_PATH)
    logger.info("   TLS Protocol:       %s", config.TLS_PROTOCOL_VERSION)
    logger.info("   Certificate:        %s", local_cert)
    logger.info("   Frontend Dir:       %s", args.frontend)
    logger.info("=" * 70)
    logger.info("✓ Bridge ready — Open browser to https://%s:%d", 
               config.DEFAULT_LOCAL_HOST, args.local_port)
    logger.info("=" * 70)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down bridge...")
    finally:
        server.shutdown()
        logger.info("Bridge stopped.")

if __name__ == "__main__":
    main()