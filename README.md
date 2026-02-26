# Antenne

A Python daemon for [fourmilière](https://github.com/LeTamanoir/antenne) that monitors the host and sends daily reports + threshold alerts via Telegram.

## Features

- 🌡️ NVMe and HDD temperatures
- 💾 Disk usage for movies, TV, and system drives
- 🧠 RAM usage
- 🐳 Docker container status
- ⚠️ Instant alerts when thresholds are crossed
- 📅 Daily digest report at 8am

## Setup

### 1. Configure environment

Create a `.env` file with at minimum your Telegram credentials. All other values have sensible defaults — see the configuration table below.

### 2. Add the service to your `docker-compose.yml`

```yaml
antenne:
  image: ghcr.io/letamanoir/antenne:latest
  restart: unless-stopped
  env_file: /opt/antenne/.env
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
  # Required on older kernels (Linux 4.x) — Docker's default seccomp profile
  # blocks socket.socketpair(), which Python's asyncio event loop needs.
  security_opt:
    - seccomp=unconfined
```

### 3. Start it

```bash
docker compose up -d antenne
```

The container runs as a daemon: daily report at 8am, alert checks every 15 minutes (both configurable via env vars).

## Configuration

All values are configurable via environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_TOKEN` | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID |
| `NVME_DEVICES` | `/dev/nvme0:NVMe` | Comma-separated `device:label` pairs |
| `HDD_DEVICES` | `/dev/sda:HDD sda,/dev/sdb:HDD sdb` | Comma-separated `device:label` pairs |
| `DISK_MOUNTS` | `/mnt/movies:Movies Drive,/mnt/tv:TV Drive,/:System Disk` | Comma-separated `path:label` pairs |
| `NVME_WARN_TEMP` | `70` | NVMe warning threshold (°C) |
| `NVME_CRIT_TEMP` | `80` | NVMe critical threshold (°C) |
| `HDD_WARN_TEMP` | `45` | HDD warning threshold (°C) |
| `HDD_CRIT_TEMP` | `50` | HDD critical threshold (°C) |
| `DISK_WARN_PERCENT` | `80` | Disk usage warning threshold (%) |
| `DISK_CRIT_PERCENT` | `90` | Disk usage critical threshold (%) |
| `RAM_WARN_PERCENT` | `85` | RAM usage warning threshold (%) |
| `REPORT_HOUR` | `8` | Hour of day for the daily report (0–23) |
| `ALERT_INTERVAL_MINUTES` | `15` | How often to check for alerts (minutes) |
