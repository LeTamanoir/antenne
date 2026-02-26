# NAS Monitor

A Python daemon that monitors your NAS and sends daily reports + threshold alerts via Telegram.

## Features

- 🌡️ NVMe and HDD temperatures
- 💾 Disk usage for movies, TV, and system drives
- 🧠 RAM usage
- 🐳 Docker container status
- ⚠️ Instant alerts when thresholds are crossed
- 📅 Daily digest report at 8am

## Setup

### 1. Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in your Telegram bot token and chat ID.

### 2. Add the service to your `docker-compose.yml`

```yaml
nas-monitor:
  build: /opt/fourmiliere_bot
  restart: unless-stopped
  env_file: /opt/fourmiliere_bot/.env
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock
    - /mnt/movies:/mnt/movies:ro
    - /mnt/tv:/mnt/tv:ro
  devices:
    - /dev/nvme0:/dev/nvme0
    - /dev/sda:/dev/sda
    - /dev/sdb:/dev/sdb
  cap_add:
    - SYS_RAWIO
```

### 3. Start it

```bash
docker compose up -d --build nas-monitor
```

The container runs as a daemon: daily report at 8am, alert checks every 15 minutes.

## Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| NVMe temp | 70°C | 80°C |
| HDD temp | 45°C | 50°C |
| Disk usage | 80% | 90% |
| RAM usage | 85% | - |

You can adjust thresholds in the `THRESHOLDS` dict in `monitor.py`.
