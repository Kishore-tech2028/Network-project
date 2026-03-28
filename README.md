# Secure Multi-Client Quiz System

A real-time quiz platform with two modes:

- **Web mode**: HTTPS + WSS quiz app for browsers
- **Socket mode**: TLS CLI server/client for terminal-based participants

## Project Structure

- `Server/` — core server logic, TLS cert files, web frontend, quiz state manager
- `client/` — optional local bridge and browser client assets

Main files:

- `Server/web_server.py` — HTTPS + WebSocket server
- `Server/quiz_socket_server.py` — TLS socket quiz server
- `Server/quiz_socket_client.py` — terminal client
- `Server/session_manager.py` — shared quiz state and rules

## Features

- Multi-client real-time quiz flow
- Host/player role handling
- Quiz countdown and synchronized question delivery
- Live leaderboard updates
- TLS-secured transport

## Requirements

- Python 3.10+
- Open ports:
  - `8443` for web mode
  - `12345` for socket mode
- Certificate and key in `Server/`:
  - `server.crt`
  - `server.key`

## Run

### 1) Web mode (browser clients)

From repository root:

```bash
python Server/web_server.py
```

Open in browser:

```text
https://<server-ip>:8443
```

For local testing:

```text
https://127.0.0.1:8443
```

### 2) Socket mode (CLI clients)

Start TLS socket server:

```bash
python Server/quiz_socket_server.py
```

Start host client:

```bash
python Server/quiz_socket_client.py --username HostUser --host-mode
```

Start player client:

```bash
python Server/quiz_socket_client.py --username Alice
```

Useful CLI options:

- `--host` (default `127.0.0.1`)
- `--port` (default `12345`)
- `--ca-cert` (default `Server/server.crt`)

## Protocol (high-level)

Client actions:

- `join`
- `start_quiz`
- `submit_answer`
- `ping`

Server messages include:

- `welcome`
- `participants_update`
- `quiz_countdown`
- `question`
- `answer_result`
- `question_closed`
- `quiz_finished`

## Troubleshooting

- If TLS connection fails, verify `Server/server.crt` and `Server/server.key` exist and match.
- If browser warns about certificate trust, this is expected with self-signed certificates in local setups.
- If clients cannot connect, verify host/port and firewall settings.

## Notes

This repository uses low-level Python socket programming (`socket`, `ssl`, threads) for both TLS and WebSocket handling, with `SessionManager` as the central quiz state engine.
