# NAS Monitor

A simple Python script that monitors your NAS and sends daily reports + threshold alerts via Telegram.

## Features

- 🌡️ NVMe and HDD temperatures
- 💾 Disk usage for movies, TV, and system drives
- 🧠 RAM usage
- 🐳 Docker container status
- ⚠️ Instant alerts when thresholds are crossed
- 📅 Daily digest report

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in your Telegram bot token and chat ID.

### 3. Test it

```bash
python3 monitor.py
```

### 4a. Run with Docker (recommended)

Add a `nas-monitor` service to your `docker-compose.yml`:

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

Then start it:

```bash
docker compose up -d --build nas-monitor
```

The container runs in daemon mode: daily report at 8am, alert checks every 15 minutes.

### 4b. Set up cron jobs (alternative)

```bash
crontab -e
```

Add these lines:

```
# Daily report at 8am
0 8 * * * cd /opt/fourmiliere_bot && python3 monitor.py

# Alert check every 15 minutes
*/15 * * * * cd /opt/fourmiliere_bot && python3 monitor.py --alert-only
```

## Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| NVMe temp | 70°C | 80°C |
| HDD temp | 45°C | 50°C |
| Disk usage | 80% | 90% |
| RAM usage | 85% | - |

You can adjust thresholds in the `THRESHOLDS` dict in `monitor.py`.

## Usage

```bash
# Send full daily report now
python3 monitor.py

# Only send message if thresholds are crossed
python3 monitor.py --alert-only

# Run as long-lived daemon (used by Docker)
python3 monitor.py --daemon
```
