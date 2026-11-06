"""The Dashboard's System card: swap traffic beside how full the swap is."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.web import system_usage


@pytest.fixture
def vmstat(monkeypatch, tmp_path):
    """/proc/vmstat as a file the test writes, and a clock it moves."""
    path = tmp_path / "vmstat"
    real = Path

    def fake(name, *rest):
        return path if str(name) == "/proc/vmstat" else real(name, *rest)

    clock = [100.0]
    monkeypatch.setattr(system_usage, "Path", fake)
    monkeypatch.setattr(system_usage.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(system_usage.os, "sysconf", lambda name: 4096)
    monkeypatch.setattr(system_usage, "_LAST_SWAP", None)

    def write(swapped_in: int, swapped_out: int, at: float) -> None:
        path.write_text(f"nr_free_pages 1\npswpin {swapped_in}\npswpout {swapped_out}\n", encoding="ascii")
        clock[0] = at

    return write


def test_swap_traffic_is_a_rate_between_two_reads(vmstat):
    vmstat(1000, 5000, at=100.0)
    assert system_usage._swap_traffic() == {"swap_in_per_second": None, "swap_out_per_second": None}
    # 512 pages in and none out over two seconds.
    vmstat(1512, 5000, at=102.0)
    assert system_usage._swap_traffic() == {"swap_in_per_second": 512 * 4096 / 2, "swap_out_per_second": 0.0}
