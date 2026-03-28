"""
config.py — Server Configuration Constants
───────────────────────────────────────────
Centralized configuration for web server, SSL/TLS,
WebSocket, and quiz timing parameters.
"""

# ── Server Binding & SSL ──────────────────────────────────────
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8443
DEFAULT_CERT_FILE = "server.crt"
DEFAULT_KEY_FILE = "server.key"

# ── Quiz Configuration ────────────────────────────────────────
DEFAULT_QUESTIONS_FILE = "questions.json"

# ── TLS/SSL Protocol ─────────────────────────────────────────
TLS_PROTOCOL_VERSION = "TLS 1.2+"

# ── Logging Configuration ─────────────────────────────────────
LOG_FORMAT = "%(asctime)s  [%(levelname)s]  %(name)s — %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
LOG_LEVEL = "INFO"
