"""The sidebar's VPN dot, read from the tunnel qBittorrent actually uses."""

from __future__ import annotations

import pytest

from bankai.web import app as app_mod


@pytest.fixture()
def gluetun(monkeypatch):
    """Answer gluetun's control-server routes from a dict."""
    answers: dict = {}

    def fake(path, *, method="GET", json_body=None):
        value = answers[path]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(app_mod, "_gluetun_get", fake)
    return answers


def test_connected_means_running_and_traffic_actually_out(gluetun):
    gluetun["/v1/vpn/status"] = {"status": "running"}
    gluetun["/v1/publicip/ip"] = {"public_ip": "159.26.104.39", "city": "Frankfurt am Main", "country": "Germany"}
    gluetun["/v1/portforward"] = {"port": 63583}

    status = app_mod._gluetun_vpn_status()

    assert status["connected"] is True
    assert status["public_ip"] == "159.26.104.39"
    assert status["forwarded_port"] == 63583
    assert "Frankfurt am Main, Germany" in status["detail"]


def test_running_without_a_public_ip_is_not_connected(gluetun):
    """gluetun can say running for a moment after a server stops answering.

    A green dot there would be the one lie that matters: it would say torrents
    are protected while nothing is getting out at all.
    """
    gluetun["/v1/vpn/status"] = {"status": "running"}
    gluetun["/v1/publicip/ip"] = {"public_ip": ""}
    gluetun["/v1/portforward"] = {"port": 0}

    assert app_mod._gluetun_vpn_status()["connected"] is False


def test_a_stopped_tunnel_is_disconnected(gluetun):
    gluetun["/v1/vpn/status"] = {"status": "stopped"}
    gluetun["/v1/publicip/ip"] = {"public_ip": "159.26.104.39"}
    gluetun["/v1/portforward"] = {"port": 63583}

    assert app_mod._gluetun_vpn_status()["status"] == "disconnected"


def test_an_unreachable_control_server_is_unavailable_not_connected(gluetun):
    gluetun["/v1/vpn/status"] = OSError("connection refused")

    status = app_mod._gluetun_vpn_status()
    assert status["connected"] is False
    assert status["status"] == "unavailable"
    assert "connection refused" in status["detail"]


def test_a_missing_forwarded_port_does_not_hide_the_tunnel(gluetun):
    """Port forwarding is a speed feature; losing it is not losing the VPN."""
    gluetun["/v1/vpn/status"] = {"status": "running"}
    gluetun["/v1/publicip/ip"] = {"public_ip": "159.26.104.39"}
    gluetun["/v1/portforward"] = OSError("no port yet")

    status = app_mod._gluetun_vpn_status()
    assert status["connected"] is True
    assert status["forwarded_port"] is None


def test_without_a_control_url_the_laptop_is_asked_as_before(monkeypatch):
    monkeypatch.setattr(app_mod, "_laptop_vpn_status", lambda: {"status": "laptop"})
    monkeypatch.setattr(app_mod, "_gluetun_vpn_status", lambda: {"status": "gluetun"})

    class Settings:
        class vpn:
            control_url = ""

    monkeypatch.setattr(app_mod, "get_settings", lambda: Settings)
    assert app_mod._vpn_status() == {"status": "laptop"}
    Settings.vpn.control_url = "http://127.0.0.1:8000"
    assert app_mod._vpn_status() == {"status": "gluetun"}


def test_the_key_is_sent_as_a_header(monkeypatch):
    """gluetun stops answering anonymously after v3.40."""
    seen = {}

    class Response:
        content = b'{"status": "running"}'

        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "running"}

    def fake_request(method, url, *, headers, json, timeout):
        seen.update(method=method, url=url, headers=headers)
        return Response()

    import httpx

    monkeypatch.setattr(httpx, "request", fake_request)

    class Settings:
        class vpn:
            control_url = "http://127.0.0.1:8000/"
            api_key = "secret-key"

    monkeypatch.setattr(app_mod, "get_settings", lambda: Settings)
    app_mod._gluetun_get("/v1/vpn/status")
    assert seen["url"] == "http://127.0.0.1:8000/v1/vpn/status"
    assert seen["headers"] == {"X-API-Key": "secret-key"}
