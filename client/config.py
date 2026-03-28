"""
config.py — Client Bridge Configuration Constants
──────────────────────────────────────────────────
Centralized configuration for local HTTPS/WebSocket bridge.
"""

# ── Local Bridge Binding ──────────────────────────────────────
DEFAULT_LOCAL_HOST = "127.0.0.1"
DEFAULT_LOCAL_PORT = 9443

# ── Upstream Server (Central) ─────────────────────────────────
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8443

# ── Bridge Certificates ──────────────────────────────────────
DEFAULT_CERT_FILE = "server.crt"
DEFAULT_KEY_FILE = "server.key"

# ── Upstream WSS Connection ──────────────────────────────────
UPSTREAM_WS_PATH = "/ws"

# ── TLS/SSL Protocol ────────────────────────────────────────
TLS_PROTOCOL_VERSION = "TLS 1.2+"

# ── Logging Configuration ───────────────────────────────────
LOG_FORMAT = "%(asctime)s  [%(levelname)s]  %(name)s — %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
LOG_LEVEL = "INFO"
