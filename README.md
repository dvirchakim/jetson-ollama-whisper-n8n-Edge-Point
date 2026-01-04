# Jetson Edge AI Point

Deploy **Ollama (LLM)** and **Whisper (Speech-to-Text)** as isolated Docker services on a **Jetson AGX Xavier**, accessible via dedicated LAN IPs for integration with **n8n AI Agents**.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Jetson AGX Xavier 32GB                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │     Ollama      │  │     Whisper     │  │   Watchtower    │              │
│  │  (Gemma3 LLM)   │  │  (Speech→Text)  │  │  (Auto-update)  │              │
│  │  <OLLAMA_IP>    │  │  <WHISPER_IP>   │  │     bridge      │              │
│  │    :11434       │  │     :9000       │  │                 │              │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────┘              │
│           │                    │                                            │
│           └────────┬───────────┘                                            │
│                    │ macvlan + bridge                                       │
└────────────────────┼────────────────────────────────────────────────────────┘
                     │
              ┌──────┴──────┐
              │  LAN Switch │
              └──────┬──────┘
                     │
         ┌───────────┴───────────┐
         │                       │
    ┌────┴────┐            ┌─────┴─────┐
    │   n8n   │            │  Clients  │
    │  Agent  │            │           │
    └─────────┘            └───────────┘
```

---




<img width="1623" height="833" alt="image" src="https://github.com/user-attachments/assets/f12d8f85-8ee5-47d6-b922-da7e6e5c3e77" />


<img width="1886" height="795" alt="image" src="https://github.com/user-attachments/assets/0c82f078-59b2-455c-9311-bafe626e1ab5" />

## Features

- **Ollama** with `PetrosStav/gemma3-tools:4b` model (tool-calling enabled)
- **Whisper** speech-to-text API (faster-whisper with GPU acceleration)
- **macvlan networking** — each service gets its own LAN IP
- **Watchtower** for automatic container updates
- **Single-command install/uninstall** scripts
- **Jetson-optimized** with NVIDIA container runtime

---

## Requirements

| Component | Requirement |
|-----------|-------------|
| Hardware | NVIDIA Jetson AGX Xavier 32GB |
| Kernel | `Linux 5.15.148-tegra` or newer (tested on R36.4.4) |
| Architecture | ARM64 (aarch64) |
| Network | Ethernet interface (e.g., `eth0`, `eno1`) |
| Credentials | Google API Key + Custom Search CX ID (for search tool) |

---

## Quick Start

### 1. Clone the Repository

```bash
git clone git@github.com:dvirchakim/jetson-ollama-whisper-n8n-Edge-Point.git
cd jetson-ollama-whisper-n8n-Edge-Point
```

### 2. Configure Environment

```bash
cp .env.template .env
nano .env
```

Edit the following values:

```bash
# Network interface (check with: ip link show)
HOST_INTERFACE=eth0

# Your LAN configuration (adjust to match your network)
NETWORK_SUBNET=192.168.0.0/24
NETWORK_GATEWAY=192.168.0.1
MACVLAN_IP_RANGE=192.168.0.232/29

# Static IPs for services (must be within MACVLAN_IP_RANGE)
OLLAMA_IP=192.168.0.233
WHISPER_IP=192.168.0.234

# Google Search credentials (for tool-calling)
GOOGLE_API_KEY=your_api_key_here
GOOGLE_CX_ID=your_cx_id_here
```

### 3. Install

```bash
sudo ./install.sh
```

This will:
- Install Docker + Docker Compose (if needed)
- Configure NVIDIA container runtime for Jetson
- Create macvlan network
- Deploy Ollama, Whisper, and Watchtower
- Pull the Gemma3 model
- Set up host-to-container routing

### 4. Verify

```bash
# Check container status
docker ps

# Test Ollama (replace with your OLLAMA_IP)
curl http://<OLLAMA_IP>:11434/api/tags

# Test Whisper (replace with your WHISPER_IP)
curl http://<WHISPER_IP>:9000/health
```

---

## Service Endpoints

| Service | Port | Endpoint |
|---------|------|----------|
| Ollama | 11434 | `http://<OLLAMA_IP>:11434` |
| Whisper | 9000 | `http://<WHISPER_IP>:9000` |

> Replace `<OLLAMA_IP>` and `<WHISPER_IP>` with the values from your `.env` file.

---

## API Usage

### Ollama — Chat Completion

```bash
curl http://<OLLAMA_IP>:11434/api/chat -d '{
  "model": "PetrosStav/gemma3-tools:4b",
  "messages": [{"role": "user", "content": "What is the capital of France?"}],
  "stream": false
}'
```

### Ollama — With Tool Calling

```bash
curl http://<OLLAMA_IP>:11434/api/chat -d '{
  "model": "PetrosStav/gemma3-tools:4b",
  "messages": [{"role": "user", "content": "Search the web for latest AI news"}],
  "tools": [{
    "type": "function",
    "function": {
      "name": "web_search",
      "description": "Search the web using Google",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "Search query"}
        },
        "required": ["query"]
      }
    }
  }],
  "stream": false
}'
```

### Whisper — Transcribe Audio

```bash
curl -X POST http://<WHISPER_IP>:9000/asr \
  -F "audio_file=@recording.wav" \
  -F "output=json"
```

### Whisper — With Language Hint

```bash
curl -X POST http://<WHISPER_IP>:9000/asr \
  -F "audio_file=@recording.wav" \
  -F "language=en" \
  -F "output=json"
```

### Whisper — OpenAI-Compatible Endpoint

```bash
curl -X POST http://<WHISPER_IP>:9000/v1/audio/transcriptions \
  -F "file=@recording.wav" \
  -F "model=whisper-1"
```

---

## n8n Integration

### HTTP Request Node — Ollama

| Field | Value |
|-------|-------|
| Method | POST |
| URL | `http://<OLLAMA_IP>:11434/api/chat` |
| Body Type | JSON |
| Body | See chat example above |

### HTTP Request Node — Whisper

| Field | Value |
|-------|-------|
| Method | POST |
| URL | `http://<WHISPER_IP>:9000/asr` |
| Body Type | Form-Data |
| Body | `audio_file`: Binary data |

### Example n8n Workflow

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   Trigger    │───▶│   Whisper    │───▶│   Ollama     │
│  (Webhook)   │    │  Transcribe  │    │   Process    │
└──────────────┘    └──────────────┘    └──────────────┘
       │                   │                   │
       │              Audio → Text        Text → Response
       │                   │                   │
       └───────────────────┴───────────────────┘
```

---

## IP Layout Example

```
Example: Network 192.168.0.0/24, Gateway 192.168.0.1

┌─────────────────────────────────────────────────────┐
│ DHCP Range: 192.168.0.2 - 192.168.0.230             │
│ (Exclude macvlan range from DHCP server)           │
├─────────────────────────────────────────────────────┤
│ macvlan Range: 192.168.0.232/29                     │
│   .232 - Network (reserved)                         │
│   .233 - Ollama                                     │
│   .234 - Whisper                                    │
│   .235 - (available)                                │
│   .236 - (available)                                │
│   .237 - (available)                                │
│   .238 - (available)                                │
│   .239 - Broadcast (reserved)                       │
├─────────────────────────────────────────────────────┤
│ Host shim: 192.168.0.254 (for Jetson→container)    │
└─────────────────────────────────────────────────────┘
```

---

## File Structure

```
jetson-ollama-whisper-n8n-Edge-Point/
├── README.md              # This documentation
├── .env.template          # Environment template
├── .env                   # Your configuration (git-ignored)
├── docker-compose.yml     # Service definitions
├── install.sh             # Installation script
├── uninstall.sh           # Cleanup script
└── whisper-service/       # Custom Whisper HTTP API
    ├── Dockerfile         # ARM64 Jetson-compatible build
    └── server.py          # FastAPI server with /asr endpoint
```

---

## Uninstall

### Standard Cleanup

```bash
sudo ./uninstall.sh
```

Removes:
- All containers (ollama, whisper, watchtower)
- Docker networks (macvlan)
- Docker volumes (model data)
- Docker images
- macvlan host shim
- Environment files

### Full Cleanup (Including Docker)

```bash
sudo ./uninstall.sh -y -d
```

Also removes:
- Docker engine
- NVIDIA container runtime
- All Docker data

---

## Troubleshooting

### Container won't start

```bash
# Check logs
docker compose logs ollama
docker compose logs whisper

# Check NVIDIA runtime
docker info | grep -i runtime
```

### Cannot reach container from Jetson host

The macvlan driver isolates containers from the host by default. The install script creates a "shim" interface. Verify:

```bash
ip link show macvlan-shim
ip route | grep <OLLAMA_IP>
```

If missing, restart the shim service:

```bash
sudo systemctl restart macvlan-shim.service
```

### Cannot reach container from LAN

1. Verify the container has the correct IP:
   ```bash
   docker inspect ollama | grep IPAddress
   ```

2. Check macvlan network exists:
   ```bash
   docker network ls | grep macvlan
   ```

3. Ensure the IP range is excluded from your DHCP server

### Ollama model not loading

```bash
# Check available models
docker exec ollama ollama list

# Pull model manually
docker exec ollama ollama pull PetrosStav/gemma3-tools:4b

# Check GPU access
docker exec ollama nvidia-smi
```

### Whisper returns errors

```bash
# Check Whisper logs
docker compose logs whisper

# Verify GPU access
docker exec whisper nvidia-smi

# Test with simple request
curl -v http://<WHISPER_IP>:9000/health
```

### Network interface not found

```bash
# List available interfaces
ip link show

# Update .env with correct interface name
nano .env
# Change HOST_INTERFACE=eth0 to your interface
```

---

## Tested Configuration

| Component | Version/Value |
|-----------|---------------|
| Hardware | Jetson AGX Xavier 32GB |
| L4T Release | R36.4.4 |
| Kernel | `5.15.148-tegra` |
| Architecture | `aarch64` |
| JetPack | 6.x |
| CUDA | 12.6 |
| Docker | 29.x |
| Docker Compose | 2.x+ |
| Whisper Base | `dustynv/faster-whisper:r36.4.0-cu128-24.04` |
| Ollama | `ollama/ollama:latest` |

---

## Security Notes

- **API Keys**: Store `GOOGLE_API_KEY` and `GOOGLE_CX_ID` in `.env` (git-ignored)
- **Network**: macvlan exposes containers directly to LAN — use firewall rules if needed
- **No Auth**: Services have no built-in authentication — deploy behind reverse proxy for production

---

## License

MIT License — See [LICENSE](LICENSE) for details.

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Submit a pull request

---

## Support

- **Issues**: [GitHub Issues](https://github.com/dvirchakim/jetson-ollama-whisper-n8n-Edge-Point/issues)
- **Discussions**: [GitHub Discussions](https://github.com/dvirchakim/jetson-ollama-whisper-n8n-Edge-Point/discussions)
