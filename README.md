# Antenne

A Python daemon that monitors the host and sends daily reports + threshold alerts via Telegram.

## Features

- 🌡️ NVMe and HDD temperatures
- 💾 Disk usage for movies, TV, and system drives
- 🧠 RAM usage
- ⚠️ Instant alerts when thresholds are crossed
- 📅 Daily digest report at a configurable time (default 8:00)
- 📩 Immediate report on daemon startup
- 📬 On-demand report via `/report` Telegram command

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
    - /mnt/movies:/mnt/movies:ro
    - /mnt/tv:/mnt/tv:ro
  devices:
    - /dev/nvme0:/dev/nvme0
    - /dev/sda:/dev/sda
    - /dev/sdb:/dev/sdb
  cap_add:
    - SYS_RAWIO
    - SYS_ADMIN
```
### 3. Start it

```bash
docker compose up -d antenne
```

The container runs as a daemon: sends a report on startup, then again daily at 8:00 by default, with alert checks every 15 minutes (all configurable via env vars). You can also trigger an on-demand report by sending `/report` in the Telegram chat.

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
| `REPORT_MINUTE` | `0` | Minute of the hour for the daily report (0–59) |
| `ALERT_INTERVAL_MINUTES` | `15` | How often to check for alerts (minutes) |
| `REPORT_NVME` | `true` | Include NVMe temperatures in the report |
| `REPORT_HDD` | `true` | Include HDD temperatures in the report |
| `REPORT_DISK` | `true` | Include disk usage in the report |
| `REPORT_RAM` | `true` | Include RAM usage in the report |
