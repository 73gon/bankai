"""How busy the machine is, for the Dashboard: CPU, memory, GPU, network.

Read from ``/proc`` (the WSL distro's own view: its CPUs and the memory WSL
gives it) and from ``nvidia-smi`` (WSL's copy under ``/usr/lib/wsl/lib``),
which also says how hard NVENC works -- the Encode page's encodes. CPU and
network are rates, so each read is compared with the one before it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_LAST_CPU: tuple[int, int] | None = None
_LAST_NET: tuple[float, int, int] | None = None
_PEAK_NET = 0.0
_GPU: tuple[float, list[dict[str, Any]] | None] = (0.0, None)
# nvidia-smi takes a few hundred milliseconds; asked at most this often.
_GPU_SECONDS = 2.0
_GPU_FIELDS = (
    "name", "utilization.gpu", "utilization.encoder", "utilization.decoder",
    "memory.used", "memory.total", "temperature.gpu", "power.draw", "power.limit",
)


def _nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    wsl = Path("/usr/lib/wsl/lib/nvidia-smi")
    return str(wsl) if wsl.is_file() else None


def _number(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None  # "[N/A]", "[Not Supported]"


def _gpus() -> list[dict[str, Any]] | None:
    """Each NVIDIA GPU's load, memory, temperature and power; None without one."""
    global _GPU
    if time.monotonic() - _GPU[0] < _GPU_SECONDS:
        return _GPU[1]
    binary = _nvidia_smi()
    rows: list[dict[str, Any]] | None = None
    if binary:
        try:
            out = subprocess.run(
                [binary, "--query-gpu=" + ",".join(_GPU_FIELDS), "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout
            rows = []
            for line in out.strip().splitlines():
                cells = [cell.strip() for cell in line.split(",")]
                if len(cells) != len(_GPU_FIELDS):
                    continue
                name, *numbers = cells
                gpu, encoder, decoder, used, total, temperature, power, limit = (_number(cell) for cell in numbers)
                rows.append({
                    "name": name,
                    "percent": gpu,
                    "encoder_percent": encoder,
                    "decoder_percent": decoder,
                    # nvidia-smi says MiB.
                    "memory_used": int(used * 2**20) if used is not None else None,
                    "memory_total": int(total * 2**20) if total is not None else None,
                    "temperature": temperature,
                    "power_watts": power,
                    "power_limit_watts": limit,
                })
        except (OSError, subprocess.SubprocessError):
            rows = None
    _GPU = (time.monotonic(), rows)
    return rows


def _cpu() -> dict[str, Any]:
    """Share of the CPUs busy since the last read, the load average and the core count."""
    global _LAST_CPU
    with Path("/proc/stat").open(encoding="ascii") as handle:
        values = [int(value) for value in handle.readline().split()[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    total = sum(values[:8])
    percent = None
    if _LAST_CPU is not None and total > _LAST_CPU[1]:
        percent = 100.0 * (1 - (idle - _LAST_CPU[0]) / (total - _LAST_CPU[1]))
    _LAST_CPU = (idle, total)
    load = os.getloadavg()
    return {"percent": percent, "cores": os.cpu_count(), "load": [round(value, 2) for value in load]}


def _memory() -> dict[str, Any]:
    info: dict[str, int] = {}
    with Path("/proc/meminfo").open(encoding="ascii") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", 0)
    swap_total = info.get("SwapTotal", 0)
    return {
        "total": total,
        "used": total - available,
        # Page cache: memory Linux gives back when asked, counted apart from what is used.
        "cached": info.get("Cached", 0) + info.get("Buffers", 0),
        "swap_total": swap_total,
        "swap_used": swap_total - info.get("SwapFree", 0),
    }


def _network() -> dict[str, Any]:
    """Bytes a second in and out since the last read, every interface but loopback, tunnels and
    docker's bridges (their traffic crosses eth0 too), what went each way since the distro booted,
    and the busiest second seen since bankai-web started: the meters' scale, as a link's speed
    is not known."""
    global _LAST_NET, _PEAK_NET
    received = sent = 0
    with Path("/proc/net/dev").open(encoding="ascii") as handle:
        for line in handle.readlines()[2:]:
            name, _, rest = line.partition(":")
            name = name.strip()
            if name == "lo" or name.startswith(("tailscale", "docker", "veth", "br-")):
                continue
            fields = rest.split()
            received += int(fields[0])
            sent += int(fields[8])
    now = time.monotonic()
    out: dict[str, Any] = {
        "received_per_second": None, "sent_per_second": None,
        "received_total": received, "sent_total": sent,
    }
    if _LAST_NET is not None and now > _LAST_NET[0]:
        seconds = now - _LAST_NET[0]
        out["received_per_second"] = max(0.0, (received - _LAST_NET[1]) / seconds)
        out["sent_per_second"] = max(0.0, (sent - _LAST_NET[2]) / seconds)
        _PEAK_NET = max(_PEAK_NET, out["received_per_second"], out["sent_per_second"])
    out["peak_per_second"] = _PEAK_NET or None
    _LAST_NET = (now, received, sent)
    return out


def sample() -> dict[str, Any]:
    """One reading of the machine. The first one has no CPU or network rate yet."""
    with _LOCK:
        out: dict[str, Any] = {"at": time.time(), "host": os.uname().nodename}
        for key, read in (("cpu", _cpu), ("memory", _memory), ("network", _network)):
            try:
                out[key] = read()
            except (OSError, ValueError, IndexError):
                out[key] = None
        out["gpus"] = _gpus()
        try:
            with Path("/proc/uptime").open(encoding="ascii") as handle:
                out["uptime_seconds"] = float(handle.read().split()[0])
        except (OSError, ValueError):
            out["uptime_seconds"] = None
        return out


__all__ = ["sample"]
