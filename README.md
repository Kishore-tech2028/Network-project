# Secure Multi-Client Quiz System

A real-time quiz platform built with Python sockets, TLS, HTTPS, and WebSockets.

This project is currently run in **web mode** using:

- `Server/web_server.py` (central HTTPS + WSS server)
- `client/local_bridge.py` (optional client-side bridge)

## 1) Introduction

The system supports a host and multiple participants in a synchronized quiz session.
All clients receive real-time updates for:

- lobby/participants list
- countdown to quiz start
- question delivery and timeout
- answer result and leaderboard

Security is provided using TLS (HTTPS + WSS).

## 2) Project Layout

- `Server/web_server.py` — main HTTPS/WebSocket quiz server
- `Server/session_manager.py` — thread-safe quiz logic and state
- `Server/client_handler.py` — shared client handling utilities
- `Server/questions.json` — quiz question bank
- `Server/server.crt`, `Server/server.key` — TLS certificate and key
- `Server/frontend/` — browser UI served by the web server
- `client/local_bridge.py` —  local bridge for client machines
- `client/frontend/` —  client-side static assets

## 3) Prerequisites

- Python 3.10 or newer
- Linux/macOS/Windows terminal access
- Port `8443` open on server machine
- Valid TLS files in `Server/`:
  - `server.crt`
  - `server.key`

## 4) Setup

From project root:

```bash
cd {PATH}/CN
```

Optional: verify Python version:

```bash
python --version
```

## 5) Running the System

### A. Start the central web server

Run on the machine hosting the quiz:

```bash
python Server/web_server.py
```

By default, server binds to:

- host: `0.0.0.0`
- port: `8443`

Custom host/port example:

```bash
python Server/web_server.py --host 0.0.0.0 --port 8443
```

### B. Find the server IP address

On Linux server machine:

```bash
hostname -I
```

Use the LAN IP from output (example: `192.168.1.25`).

### C. Connect browser clients

- Same machine: `https://127.0.0.1:8443`
- Other devices on LAN: `https://<SERVER_IP>:8443`
  - Example: `https://192.168.1.25:8443`

Browser may show a certificate warning (self-signed cert). Accept/continue for local lab use.

## 6) Local Bridge Mode

Use this only if each client machine should connect through a local bridge.

Run bridge on client machine:

```bash
python3 local_bridge.py --quiz-host <LOCAL IP> --quiz-port <PORT> --frontend frontend 
```

Then open on that client machine:

```text
https://127.0.0.1:9443
```

## 7) Protocol Summary

Client actions:

- `join`
- `start_quiz`
- `submit_answer`
- `set_ready`
- `leave_quiz`
- `ping`

Server events:

- `welcome`
- `participants_update`
- `quiz_countdown`
- `question`
- `answer_result`
- `question_closed`
- `quiz_finished`
- `start_rejected`
- `ready_updated` / `ready_rejected`
- `error`

## 8) Quick Verification Checklist

- Host can join and start quiz
- Players appear in participant list
- Countdown is visible for all players
- Questions arrive in sync
- Leaderboard updates after each round
- Disconnecting a client updates roster correctly

## 9) Troubleshooting

- **Port in use**: change port with `--port`.
- **TLS file error**: ensure `Server/server.crt` and `Server/server.key` exist and match.
- **Client cannot connect**: verify IP address, firewall rules, and server machine reachability.
- **WebSocket issues**: ensure clients use `https://` URL and not `http://`.

## 10) Notes

This project demonstrates low-level network programming with Python standard library modules (`socket`, `ssl`, `threading`, and manual WebSocket framing), while keeping quiz state centralized in `SessionManager`.
