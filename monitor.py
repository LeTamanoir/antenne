#!/usr/bin/env python3
"""
Antenne - Sends daily reports and threshold alerts via Telegram.
"""

import os
import subprocess
import re
import argparse
import time
from datetime import datetime

import psutil
import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Device config
def _parse_device_pairs(env_val: str) -> list[tuple[str, str]]:
    """Parse 'device:label,device:label' env var into list of (device, label) tuples."""
    pairs = []
    for item in env_val.split(","):
        item = item.strip()
        if ":" in item:
            device, label = item.split(":", 1)
            pairs.append((device.strip(), label.strip()))
    return pairs


NVME_DEVICES = _parse_device_pairs(
    os.getenv("NVME_DEVICES", "/dev/nvme0:NVMe")
)
HDD_DEVICES = _parse_device_pairs(
    os.getenv("HDD_DEVICES", "/dev/sda:HDD sda,/dev/sdb:HDD sdb")
)
DISK_MOUNTS = _parse_device_pairs(
    os.getenv("DISK_MOUNTS", "/mnt/movies:Movies Drive,/mnt/tv:TV Drive,/:System Disk")
)

# Thresholds
THRESHOLDS = {
    "nvme_warn": int(os.getenv("NVME_WARN_TEMP", "70")),
    "nvme_crit": int(os.getenv("NVME_CRIT_TEMP", "80")),
    "hdd_warn": int(os.getenv("HDD_WARN_TEMP", "45")),
    "hdd_crit": int(os.getenv("HDD_CRIT_TEMP", "50")),
    "disk_warn": int(os.getenv("DISK_WARN_PERCENT", "80")),
    "disk_crit": int(os.getenv("DISK_CRIT_PERCENT", "90")),
    "ram_warn": int(os.getenv("RAM_WARN_PERCENT", "85")),
}

# Daemon config
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "8"))
ALERT_INTERVAL_MINUTES = int(os.getenv("ALERT_INTERVAL_MINUTES", "15"))


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    })


def get_nvme_temp(device: str) -> int | None:
    try:
        out = subprocess.check_output(
            ["smartctl", "-a", device], text=True
        )
        for line in out.splitlines():
            if "Temperature:" in line and "Sensor" not in line:
                return int(re.search(r"(\d+)\s+Celsius", line).group(1))
    except Exception:
        return None


def get_hdd_temp(device: str) -> int | None:
    try:
        out = subprocess.check_output(
            ["smartctl", "-a", device], text=True
        )
        for line in out.splitlines():
            if "Temperature_Celsius" in line or "Temperature:" in line:
                match = re.search(r"(\d+)$", line.strip())
                if match:
                    return int(match.group(1))
    except Exception:
        return None


def get_disk_usage(path: str) -> dict:
    usage = psutil.disk_usage(path)
    return {
        "total": usage.total // (1024 ** 3),
        "used": usage.used // (1024 ** 3),
        "percent": usage.percent,
    }


def get_ram_usage() -> dict:
    mem = psutil.virtual_memory()
    return {
        "total": mem.total // (1024 ** 3),
        "used": mem.used // (1024 ** 3),
        "percent": mem.percent,
    }


def get_docker_containers() -> list[dict]:
    try:
        out = subprocess.check_output(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            text=True
        )
        containers = []
        for line in out.strip().splitlines():
            name, status = line.split("\t")
            containers.append({
                "name": name,
                "running": status.startswith("Up"),
            })
        return containers
    except Exception:
        return []


def temp_emoji(temp: int, warn: int, crit: int) -> str:
    if temp >= crit:
        return "🔴"
    if temp >= warn:
        return "🟡"
    return "🟢"


def disk_emoji(percent: float) -> str:
    if percent >= THRESHOLDS["disk_crit"]:
        return "🔴"
    if percent >= THRESHOLDS["disk_warn"]:
        return "🟡"
    return "🟢"


def build_report() -> tuple[str, list[str]]:
    alerts = []
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines.append(f"🐜 *Antenne*")
    lines.append(f"📅 {now}\n")

    # NVMe drives
    for device, label in NVME_DEVICES:
        nvme_temp = get_nvme_temp(device)
        if nvme_temp is not None:
            emoji = temp_emoji(nvme_temp, THRESHOLDS["nvme_warn"], THRESHOLDS["nvme_crit"])
            lines.append(f"💾 *{label}:* {emoji} {nvme_temp}°C")
            if nvme_temp >= THRESHOLDS["nvme_crit"]:
                alerts.append(f"🔴 CRITICAL: {label} temp {nvme_temp}°C (threshold: {THRESHOLDS['nvme_crit']}°C)")
            elif nvme_temp >= THRESHOLDS["nvme_warn"]:
                alerts.append(f"🟡 WARNING: {label} temp {nvme_temp}°C (threshold: {THRESHOLDS['nvme_warn']}°C)")
        else:
            lines.append(f"💾 *{label}:* ❓ Unable to read")

    # HDDs
    for device, label in HDD_DEVICES:
        temp = get_hdd_temp(device)
        if temp is not None:
            emoji = temp_emoji(temp, THRESHOLDS["hdd_warn"], THRESHOLDS["hdd_crit"])
            lines.append(f"🗄️ *{label}:* {emoji} {temp}°C")
            if temp >= THRESHOLDS["hdd_crit"]:
                alerts.append(f"🔴 CRITICAL: {label} temp {temp}°C (threshold: {THRESHOLDS['hdd_crit']}°C)")
            elif temp >= THRESHOLDS["hdd_warn"]:
                alerts.append(f"🟡 WARNING: {label} temp {temp}°C (threshold: {THRESHOLDS['hdd_warn']}°C)")
        else:
            lines.append(f"🗄️ *{label}:* ❓ Unable to read")

    lines.append("")

    # Disk usage
    for path, label in DISK_MOUNTS:
        try:
            usage = get_disk_usage(path)
            emoji = disk_emoji(usage["percent"])
            lines.append(f"📁 *{label}:* {emoji} {usage['used']}GB / {usage['total']}GB ({usage['percent']}%)")
            if usage["percent"] >= THRESHOLDS["disk_crit"]:
                alerts.append(f"🔴 CRITICAL: {label} usage {usage['percent']}% (threshold: {THRESHOLDS['disk_crit']}%)")
            elif usage["percent"] >= THRESHOLDS["disk_warn"]:
                alerts.append(f"🟡 WARNING: {label} usage {usage['percent']}% (threshold: {THRESHOLDS['disk_warn']}%)")
        except Exception:
            lines.append(f"📁 *{label}:* ❓ Unable to read")

    lines.append("")

    # RAM
    ram = get_ram_usage()
    emoji = "🔴" if ram["percent"] >= THRESHOLDS["ram_warn"] else "🟢"
    lines.append(f"🧠 *RAM:* {emoji} {ram['used']}GB / {ram['total']}GB ({ram['percent']}%)")
    if ram["percent"] >= THRESHOLDS["ram_warn"]:
        alerts.append(f"🟡 WARNING: RAM usage {ram['percent']}% (threshold: {THRESHOLDS['ram_warn']}%)")

    lines.append("")

    # Docker containers
    containers = get_docker_containers()
    if containers:
        lines.append("🐳 *Docker Containers:*")
        for c in containers:
            emoji = "✅" if c["running"] else "❌"
            lines.append(f"  {emoji} `{c['name']}`")
            if not c["running"]:
                alerts.append(f"❌ ALERT: Container `{c['name']}` is DOWN!")
    else:
        lines.append("🐳 *Docker:* ❓ Unable to read containers")

    return "\n".join(lines), alerts


def send_alerts(alerts: list[str]) -> None:
    if alerts:
        alert_msg = "⚠️ *NAS Alert!*\n\n" + "\n".join(alerts)
        send_telegram(alert_msg)


def run_daemon() -> None:
    last_alert_ts = 0.0
    last_daily_date = None

    print("Antenne daemon started", flush=True)

    while True:
        now = datetime.now()

        # Daily report at configured hour
        if now.hour == REPORT_HOUR and last_daily_date != now.date():
            print(f"[{now}] Sending daily report", flush=True)
            report, alerts = build_report()
            send_telegram(report)
            send_alerts(alerts)
            last_daily_date = now.date()

        # Alert check at configured interval
        if time.time() - last_alert_ts >= ALERT_INTERVAL_MINUTES * 60:
            print(f"[{now}] Running alert check", flush=True)
            _, alerts = build_report()
            send_alerts(alerts)
            last_alert_ts = time.time()

        time.sleep(60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--alert-only", action="store_true",
                        help="Only send message if thresholds are crossed")
    parser.add_argument("--daemon", action="store_true",
                        help="Run as daemon: daily report, alerts at configured intervals")
    args = parser.parse_args()

    if args.daemon:
        run_daemon()
        return

    report, alerts = build_report()

    if args.alert_only:
        send_alerts(alerts)
    else:
        send_telegram(report)
        send_alerts(alerts)


if __name__ == "__main__":
    main()
