"""Cross-platform power source detection for power-aware background compaction."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import Literal

PowerSource = Literal["ac", "battery", "unknown"]


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def detect_power_source() -> PowerSource:
    """Return 'ac' when plugged in, 'battery' when unplugged, or 'unknown'."""
    if sys.platform == "win32":
        try:
            status = _SystemPowerStatus()
            ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
            if ok:
                if status.ACLineStatus == 1:
                    return "ac"
                if status.ACLineStatus == 0:
                    return "battery"
        except Exception:
            return "unknown"
        return "unknown"

    if sys.platform.startswith("linux"):
        supply_root = Path("/sys/class/power_supply")
        if supply_root.is_dir():
            saw_supply = False
            for online_file in supply_root.glob("*/online"):
                try:
                    saw_supply = True
                    if online_file.read_text(encoding="utf-8").strip() == "1":
                        return "ac"
                except OSError:
                    continue
            if saw_supply:
                return "battery"
    return "ac"


def interval_for_power_source(
    power_source: PowerSource,
    *,
    ac_minutes: int = 10,
    battery_minutes: int = 30,
) -> int:
    """Return polling interval in seconds tuned to current AC/battery power state."""
    minutes = battery_minutes if power_source == "battery" else ac_minutes
    return max(60, int(minutes) * 60)
