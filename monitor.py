#!/usr/bin/env python3
"""
Antenne - Sends daily reports and threshold alerts via Telegram.
"""

import os
import io
import re
import sqlite3
import signal
import telebot
from pySMART import Device
import time
import threading
from datetime import datetime, timedelta

import psutil
from dotenv import load_dotenv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

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
DB_PATH = os.getenv("DB_PATH", "/app/data/antenne.db")
DEFAULT_GRAPH_DURATION = os.getenv("DEFAULT_GRAPH_DURATION", "24h")


def send_telegram(message: str) -> None:
    try:
        bot.send_message(TELEGRAM_CHAT_ID, message)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}", flush=True)


def send_telegram_photo(photo: io.BytesIO) -> None:
    try:
        bot.send_photo(TELEGRAM_CHAT_ID, photo)
    except Exception as e:
        print(f"Failed to send Telegram photo: {e}", flush=True)

def get_nvme_temp(device: str) -> int | None:
    try:
        dev = Device(device)
        temp = dev.temperature
        if temp is None:
            print(f"pySMART {device}: temperature is None (model={dev.model}, interface={dev._interface}, attributes={dev.if_attributes.__dict__ if dev.if_attributes else None})", flush=True)
        return temp
    except Exception as e:
        print(f"get_nvme_temp({device}) error: {e}", flush=True)
    return None


def get_hdd_temp(device: str) -> int | None:
    try:
        dev = Device(device)
        temp = dev.temperature
        if temp is None:
            print(f"pySMART {device}: temperature is None (model={dev.model}, interface={dev._interface})", flush=True)
        return temp
    except Exception as e:
        print(f"get_hdd_temp({device}) error: {e}", flush=True)
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
        "total": mem.total / (1024 ** 3),
        "used": mem.used / (1024 ** 3),
        "percent": mem.percent,
    }


def init_db() -> None:
    """Create metrics table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            timestamp TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics (timestamp)
    """)
    conn.commit()
    conn.close()


def store_metrics() -> None:
    """Collect and store current metrics in SQLite."""
    now = datetime.now().isoformat()
    rows = []

    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)
    rows.append((now, "cpu_percent", cpu_percent))

    # RAM
    ram = get_ram_usage()
    rows.append((now, "ram_percent", ram["percent"]))

    # NVMe temps
    for device, label in NVME_DEVICES:
        temp = get_nvme_temp(device)
        if temp is not None:
            rows.append((now, f"temp_{label}", float(temp)))

    # HDD temps
    for device, label in HDD_DEVICES:
        temp = get_hdd_temp(device)
        if temp is not None:
            rows.append((now, f"temp_{label}", float(temp)))

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.executemany(
            "INSERT INTO metrics (timestamp, metric_name, metric_value) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"store_metrics() error: {e}", flush=True)


def query_metrics(metric_name: str, duration: timedelta) -> list[tuple[datetime, float]]:
    """Query metrics for a given name and time window."""
    since = (datetime.now() - duration).isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT timestamp, metric_value FROM metrics WHERE metric_name = ? AND timestamp >= ? ORDER BY timestamp",
            (metric_name, since),
        ).fetchall()
        conn.close()
        return [(datetime.fromisoformat(ts), val) for ts, val in rows]
    except Exception as e:
        print(f"query_metrics() error: {e}", flush=True)
        return []


def query_metric_avg(metric_name: str, duration: timedelta) -> float | None:
    """Query average value for a metric over the given time window."""
    since = (datetime.now() - duration).isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT AVG(metric_value) FROM metrics WHERE metric_name = ? AND timestamp >= ?",
            (metric_name, since),
        ).fetchone()
        conn.close()
        return row[0] if row and row[0] is not None else None
    except Exception as e:
        print(f"query_metric_avg() error: {e}", flush=True)
        return None


def parse_duration(text: str) -> timedelta:
    """Parse duration string like '24h', '7d', '30m' into timedelta."""
    text = text.strip().lower()
    match = re.match(r"^(\d+)\s*([hdm])$", text)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "h":
            return timedelta(hours=value)
        elif unit == "d":
            return timedelta(days=value)
        elif unit == "m":
            return timedelta(minutes=value)
    return timedelta(hours=24)


def cleanup_old_metrics(max_age_hours: int = 48) -> None:
    """Delete metrics older than max_age_hours."""
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"cleanup_old_metrics() error: {e}", flush=True)


def generate_graphs(duration: timedelta) -> list[io.BytesIO]:
    """Generate matplotlib graphs for the given duration. Returns list of PNG buffers."""
    graphs = []
    duration_label = _format_duration(duration)

    # Graph 1: CPU & RAM usage
    cpu_data = query_metrics("cpu_percent", duration)
    ram_data = query_metrics("ram_percent", duration)
    if cpu_data or ram_data:
        fig, ax = plt.subplots(figsize=(10, 4))
        if cpu_data:
            times, values = zip(*cpu_data)
            ax.plot(times, values, label="CPU %", color="#3498db", linewidth=1.5)
        if ram_data:
            times, values = zip(*ram_data)
            ax.plot(times, values, label="RAM %", color="#e74c3c", linewidth=1.5)
        ax.set_ylabel("%")
        ax.set_title(f"CPU & RAM Usage ({duration_label})")
        ax.legend()
        ax.set_ylim(0, 100)
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        fig.autofmt_xdate()
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        buf.seek(0)
        graphs.append(buf)
        plt.close(fig)

    # Graph 2: Disk temperatures
    temp_metrics = []
    for _, label in NVME_DEVICES:
        temp_metrics.append((f"temp_{label}", label))
    for _, label in HDD_DEVICES:
        temp_metrics.append((f"temp_{label}", label))

    has_temp_data = False
    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ["#2ecc71", "#e67e22", "#9b59b6", "#1abc9c", "#e74c3c"]
    for i, (metric_name, label) in enumerate(temp_metrics):
        data = query_metrics(metric_name, duration)
        if data:
            has_temp_data = True
            times, values = zip(*data)
            ax.plot(times, values, label=label, color=colors[i % len(colors)], linewidth=1.5)

    if has_temp_data:
        ax.set_ylabel("°C")
        ax.set_title(f"Disk Temperatures ({duration_label})")
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        fig.autofmt_xdate()
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        buf.seek(0)
        graphs.append(buf)
    plt.close(fig)

    return graphs


def _format_duration(duration: timedelta) -> str:
    """Format timedelta as human-readable string."""
    total_seconds = int(duration.total_seconds())
    if total_seconds >= 86400:
        days = total_seconds // 86400
        return f"{days}d"
    elif total_seconds >= 3600:
        hours = total_seconds // 3600
        return f"{hours}h"
    else:
        minutes = total_seconds // 60
        return f"{minutes}m"


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
    avg_duration = timedelta(hours=48)

    lines.append(f"🐜 *Antenne*")
    lines.append(f"📅 {now}\n")

    # NVMe drives
    if REPORT_NVME:
        for device, label in NVME_DEVICES:
            nvme_temp = get_nvme_temp(device)
            if nvme_temp is not None:
                emoji = temp_emoji(nvme_temp, THRESHOLDS["nvme_warn"], THRESHOLDS["nvme_crit"])
                avg = query_metric_avg(f"temp_{label}", avg_duration)
                if avg is not None:
                    lines.append(f"💾 *{label}:* {emoji} {nvme_temp}°C (avg {avg:.1f}°C)")
                else:
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
                avg = query_metric_avg(f"temp_{label}", avg_duration)
                if avg is not None:
                    lines.append(f"🗄️ *{label}:* {emoji} {temp}°C (avg {avg:.1f}°C)")
                else:
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

    # CPU
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_avg = query_metric_avg("cpu_percent", avg_duration)
    if cpu_avg is not None:
        lines.append(f"🖥️ *CPU:* {cpu_percent:.1f}% (avg {cpu_avg:.1f}%)")
    else:
        lines.append(f"🖥️ *CPU:* {cpu_percent:.1f}%")

    # RAM
    if REPORT_RAM:
        ram = get_ram_usage()
        emoji = "🔴" if ram["percent"] >= THRESHOLDS["ram_warn"] else "🟢"
        ram_avg = query_metric_avg("ram_percent", avg_duration)
        if ram_avg is not None:
            lines.append(f"🧠 *RAM:* {emoji} {ram['used']:.2f}GB / {ram['total']:.2f}GB ({ram['percent']}%, avg {ram_avg:.1f}%)")
        else:
            lines.append(f"🧠 *RAM:* {emoji} {ram['used']:.2f}GB / {ram['total']:.2f}GB ({ram['percent']}%)")
        if ram["percent"] >= THRESHOLDS["ram_warn"]:
            alerts.append(f"🟡 WARNING: RAM usage {ram['percent']}% (threshold: {THRESHOLDS['ram_warn']}%)")
        lines.append("")

    return "\n".join(lines), alerts


def send_alerts(alerts: list[str]) -> None:
    if alerts:
        alert_msg = "⚠️ *NAS Alert!*\n\n" + "\n".join(alerts)
        send_telegram(alert_msg)


@bot.message_handler(commands=["report"])
def handle_report_command(message):
    chat_id = str(message.chat.id)
    if chat_id != str(TELEGRAM_CHAT_ID):
        return
    now = datetime.now()
    # Parse optional duration argument: /report 7d, /report 24h, etc.
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) > 1:
        duration = parse_duration(parts[1])
    else:
        duration = parse_duration(DEFAULT_GRAPH_DURATION)
    print(f"[{now}] /report command received ({_format_duration(duration)})", flush=True)
    report, alerts = build_report()
    send_telegram(report)
    if alerts:
        alert_msg = "⚠️ *NAS Alert!*\n\n" + "\n".join(alerts)
        send_telegram(alert_msg)
    graphs = generate_graphs(duration)
    for graph in graphs:
        send_telegram_photo(graph)

def run_daemon() -> None:
    last_alert_ts = 0.0
    last_daily_date = None
    init_db()

    print("Antenne daemon started", flush=True)

    stop_event = threading.Event()

    def _handle_signal(signum, frame):
        print(f"Received signal {signum}, shutting down...", flush=True)
        bot.stop_polling()
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    poll_thread = threading.Thread(
        target=lambda: bot.infinity_polling(timeout=30, long_polling_timeout=30),
        daemon=True,
    )
    poll_thread.start()

    now = datetime.now()
    print(f"[{now}] Sending startup report", flush=True)
    report, alerts = build_report()
    send_telegram(report)
    send_alerts(alerts)
    store_metrics()

    while not stop_event.is_set():
        now = datetime.now()

        # Daily report at configured hour and minute
        if now.hour == REPORT_HOUR and now.minute == REPORT_MINUTE and last_daily_date != now.date():
            print(f"[{now}] Sending daily report", flush=True)
            report, alerts = build_report()
            send_telegram(report)
            send_alerts(alerts)
            graphs = generate_graphs(timedelta(hours=24))
            for graph in graphs:
                send_telegram_photo(graph)
            last_daily_date = now.date()

        # Alert check at configured interval
        if time.time() - last_alert_ts >= ALERT_INTERVAL_MINUTES * 60:
            print(f"[{now}] Running alert check", flush=True)
            _, alerts = build_report()
            send_alerts(alerts)
            last_alert_ts = time.time()
            store_metrics()
            cleanup_old_metrics()

        stop_event.wait(60)

    print("Antenne daemon shutting down...", flush=True)
    poll_thread.join(timeout=5)
    print("Antenne daemon stopped", flush=True)


def main():
    run_daemon()

if __name__ == "__main__":
    main()
