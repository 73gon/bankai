from bankai.web import snapshots


def test_a_setting_is_kept_for_every_device_and_forgotten_with_null():
    from fastapi.testclient import TestClient

    from bankai.web.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/ui/prefs").json() == {"prefs": {}}
        layout = {"list": 35.5, "detail": 64.5}
        assert client.put("/api/ui/prefs/split.anime", json={"value": layout}).json() == {"ok": True}
        assert client.get("/api/ui/prefs").json() == {"prefs": {"split.anime": layout}}
        client.put("/api/ui/prefs/split.anime", json={"value": None})
        assert client.get("/api/ui/prefs").json() == {"prefs": {}}
        assert client.put("/api/ui/prefs/Not A Key", json={"value": 1}).status_code == 422
        assert client.put("/api/ui/prefs/huge", json={"value": "x" * 5000}).status_code == 422


def test_saving_a_setting_leaves_every_tab_s_snapshot_alone():
    assert snapshots.write_tags("/api/ui/prefs/split.anime") == frozenset()
