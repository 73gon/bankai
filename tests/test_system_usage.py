"""The Dashboard's System card: network rates between two reads, totals and the busiest second."""

from __future__ import annotations

from pathlib import Path

import pytest

from bankai.web import system_usage

_HEAD = "Inter-|   Receive\n face |bytes    packets errs drop fifo frame compressed multicast|bytes\n"


def _row(name: str, received: int, sent: int) -> str:
    return f"{name}: {received} 0 0 0 0 0 0 0 {sent} 0 0 0 0 0 0 0\n"


@pytest.fixture
def netdev(monkeypatch, tmp_path):
    """/proc/net/dev as a file the test writes, and a clock it moves."""
    path = tmp_path / "dev"
    real = Path

    def fake(name, *rest):
        return path if str(name) == "/proc/net/dev" else real(name, *rest)

    clock = [100.0]
    monkeypatch.setattr(system_usage, "Path", fake)
    monkeypatch.setattr(system_usage.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(system_usage, "_LAST_NET", None)
    monkeypatch.setattr(system_usage, "_PEAK_NET", 0.0)

    def write(received: int, sent: int, at: float) -> None:
        # Loopback and docker's bridge are left out: what a container sends crosses eth0 as well.
        path.write_text(_HEAD + _row("lo", 10**9, 10**9) + _row("eth0", received, sent) + _row("br-1", 5 * 10**8, 0), encoding="ascii")
        clock[0] = at

    return write


def test_network_is_a_rate_between_two_reads_with_totals_and_the_busiest_second(netdev):
    netdev(1000, 500, at=100.0)
    first = system_usage._network()
    assert first["received_per_second"] is None and first["received_total"] == 1000 and first["peak_per_second"] is None
    netdev(5000, 1500, at=102.0)
    second = system_usage._network()
    assert (second["received_per_second"], second["sent_per_second"]) == (2000.0, 500.0)
    assert second["peak_per_second"] == 2000.0
    netdev(5100, 1600, at=103.0)
    assert system_usage._network()["peak_per_second"] == 2000.0
