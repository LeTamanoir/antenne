#!/usr/bin/env python3
"""
Antenne - Sends daily reports and threshold alerts via Telegram.
"""

import os
import signal
import subprocess
import re
import argparse
import time
import threading
from datetime import datetime

import docker
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
REPORT_MINUTE = int(os.getenv("REPORT_MINUTE", "0"))
ALERT_INTERVAL_MINUTES = int(os.getenv("ALERT_INTERVAL_MINUTES", "15"))

# Report sections
REPORT_NVME = os.getenv("REPORT_NVME", "true").lower() not in ("0", "false", "no")
REPORT_HDD = os.getenv("REPORT_HDD", "true").lower() not in ("0", "false", "no")
REPORT_DISK = os.getenv("REPORT_DISK", "true").lower() not in ("0", "false", "no")
REPORT_RAM = os.getenv("REPORT_RAM", "true").lower() not in ("0", "false", "no")
REPORT_DOCKER = os.getenv("REPORT_DOCKER", "true").lower() not in ("0", "false", "no")


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"Telegram API error: {data.get('description', data)}", flush=True)
            # Retry without Markdown if entity parsing failed
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            }, timeout=10)
            data = resp.json()
            if not data.get("ok"):
                print(f"Telegram API error (plain text retry): {data.get('description', data)}", flush=True)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}", flush=True)


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
        client = docker.from_env()
        return [
            {"name": c.name, "running": c.status == "running"}
            for c in client.containers.list(all=True)
        ]
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
    if REPORT_NVME:
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
    if REPORT_HDD:
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

    if REPORT_NVME or REPORT_HDD:
        lines.append("")

    # Disk usage
    if REPORT_DISK:
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
    if REPORT_RAM:
        ram = get_ram_usage()
        emoji = "🔴" if ram["percent"] >= THRESHOLDS["ram_warn"] else "🟢"
        lines.append(f"🧠 *RAM:* {emoji} {ram['used']}GB / {ram['total']}GB ({ram['percent']}%)")
        if ram["percent"] >= THRESHOLDS["ram_warn"]:
            alerts.append(f"🟡 WARNING: RAM usage {ram['percent']}% (threshold: {THRESHOLDS['ram_warn']}%)")
        lines.append("")

    # Docker containers
    if REPORT_DOCKER:
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


def poll_telegram_commands(stop_event: threading.Event) -> None:
    """Poll Telegram for /report commands using long-polling and respond synchronously."""
    offset = None
    while not stop_event.is_set():
        try:
            params: dict = {"timeout": 30, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params=params,
                timeout=35,
            )
            data = resp.json()
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")
                    if chat_id == str(TELEGRAM_CHAT_ID) and text.startswith("/report"):
                        now = datetime.now()
                        print(f"[{now}] /report command received via Telegram", flush=True)
                        report, alerts = build_report()
                        send_telegram(report)
                        if alerts:
                            alert_msg = "⚠️ *NAS Alert!*\n\n" + "\n".join(alerts)
                            send_telegram(alert_msg)
        except Exception as e:
            print(f"Telegram polling error: {e}", flush=True)
            stop_event.wait(10)


def run_daemon() -> None:
    last_alert_ts = 0.0
    last_daily_date = None

    print("Antenne daemon started", flush=True)

    stop_event = threading.Event()

    def _handle_signal(signum, frame):
        print(f"Received signal {signum}, shutting down...", flush=True)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    poll_thread = threading.Thread(
        target=poll_telegram_commands, args=(stop_event,), daemon=True
    )
    poll_thread.start()

    now = datetime.now()
    print(f"[{now}] Sending startup report", flush=True)
    report, alerts = build_report()
    send_telegram(report)
    send_alerts(alerts)

    while not stop_event.is_set():
        now = datetime.now()

        # Daily report at configured hour and minute
        if now.hour == REPORT_HOUR and now.minute == REPORT_MINUTE and last_daily_date != now.date():
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

        stop_event.wait(60)

    print("Antenne daemon shutting down...", flush=True)
    poll_thread.join(timeout=5)
    print("Antenne daemon stopped", flush=True)


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
