#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Generate a self-signed TLS certificate for the Quiz Server
#  Auto-detects LAN IP and includes it in the SAN
# ─────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT_FILE="${SCRIPT_DIR}/server.crt"
KEY_FILE="${SCRIPT_DIR}/server.key"
DAYS=365
SUBJECT="/C=IN/ST=TamilNadu/L=Chennai/O=QuizSystem/OU=Dev/CN=QuizServer"

# ── Auto-detect LAN IP ────────────────────────────────────────
detect_lan_ip() {
    # Try hostname -I first (space-separated list of IPs)
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [[ -n "$ip" && "$ip" != "127.0.0.1" ]]; then
        echo "$ip"
        return
    fi
    # Fallback: ip route
    ip=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {print $7}')
    if [[ -n "$ip" ]]; then
        echo "$ip"
        return
    fi
    echo ""
}

LAN_IP=$(detect_lan_ip)

echo "╔══════════════════════════════════════════════════╗"
echo "║   Quiz Server — Self-Signed TLS Certificate     ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

if [[ -n "$LAN_IP" ]]; then
    echo "[INFO] Detected LAN IP: $LAN_IP"
else
    echo "[WARN] Could not detect LAN IP. Certificate will only cover localhost."
fi
echo ""

if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
    echo "[INFO] Certificate already exists:"
    echo "       $CERT_FILE"
    echo "       $KEY_FILE"
    read -rp "Regenerate? (y/N): " answer
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
        echo "[SKIP] Keeping existing certificate."
        exit 0
    fi
fi

# Build SAN string
SAN="DNS:localhost,IP:127.0.0.1"
if [[ -n "$LAN_IP" ]]; then
    SAN="${SAN},IP:${LAN_IP}"
fi

echo "[GENERATING] Creating RSA 2048-bit key + X.509 certificate..."
echo "  SAN: $SAN"
openssl req -x509 -newkey rsa:2048 \
    -keyout "$KEY_FILE" \
    -out "$CERT_FILE" \
    -days "$DAYS" \
    -nodes \
    -subj "$SUBJECT" \
    -addext "subjectAltName=${SAN}" \
    2>/dev/null

echo ""
echo "[SUCCESS] Certificate generated:"
echo "  Certificate : $CERT_FILE"
echo "  Private Key : $KEY_FILE"
echo "  Valid for   : $DAYS days"
echo ""
echo "[VERIFY] Certificate details:"
openssl x509 -in "$CERT_FILE" -noout -subject -dates -fingerprint -sha256
echo ""
echo "[SAN] Subject Alternative Names:"
openssl x509 -in "$CERT_FILE" -noout -ext subjectAltName
echo ""
if [[ -n "$LAN_IP" ]]; then
    echo "Clients on your WiFi can connect to:"
    echo "  https://${LAN_IP}:8443"
    echo ""
fi
echo "Done! Start the server with:"
echo "  python Simple_Server/web_server.py"
