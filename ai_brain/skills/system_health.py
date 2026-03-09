"""
Skill: System Health
Monitors CPU, memory, disk, and temperature (Raspberry Pi compatible).
Reports anomalies to Discord and stores health log in memory.
"""

from __future__ import annotations

import os
import subprocess

SKILL_NAME = "system_health"
SKILL_DESCRIPTION = "Monitors Pi system health (CPU, memory, temp, disk)."
SCHEDULE_INTERVAL = 1800  # every 30 minutes


def run(memory, llm) -> str:
    stats = _collect_stats()
    stats_text = "\n".join(f"{k}: {v}" for k, v in stats.items())

    # Flag anomalies
    warnings = []
    if float(stats.get("cpu_temp_c", 0)) > 75:
        warnings.append(f"High CPU temp: {stats['cpu_temp_c']}°C")
    if float(stats.get("cpu_percent", 0)) > 90:
        warnings.append(f"High CPU usage: {stats['cpu_percent']}%")
    if float(stats.get("memory_percent", 0)) > 85:
        warnings.append(f"High memory usage: {stats['memory_percent']}%")
    if float(stats.get("disk_percent", 0)) > 90:
        warnings.append(f"Low disk space: {stats['disk_percent']}% used")

    summary = "\n".join(warnings) if warnings else f"System healthy: {stats_text}"
    memory.save_knowledge("system_health", summary, source="local")
    memory.log_event("system_health", "health_check", summary[:200])
    return summary


def _collect_stats() -> dict:
    stats: dict = {}
    try:
        import psutil
        stats["cpu_percent"] = str(psutil.cpu_percent(interval=1))
        vm = psutil.virtual_memory()
        stats["memory_percent"] = str(vm.percent)
        stats["memory_available_mb"] = str(round(vm.available / 1_048_576))
        disk = psutil.disk_usage("/")
        stats["disk_percent"] = str(disk.percent)
    except ImportError:
        pass

    # Raspberry Pi CPU temperature
    try:
        temp_path = "/sys/class/thermal/thermal_zone0/temp"
        if os.path.exists(temp_path):
            with open(temp_path) as f:
                stats["cpu_temp_c"] = str(round(int(f.read().strip()) / 1000, 1))
    except Exception:
        pass

    return stats
