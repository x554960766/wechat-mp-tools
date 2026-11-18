"""NSFW 配置面：阈值/开关/鉴黄方案的热更、校验与部署级锁定。

不变量（对应 docs/specs/nsfw-config-management.md）：
1. 配置落在 <DATA_DIR>/settings.json 的 nsfw 段，缺字段按默认补齐，改一个字段不动其它。
2. 环境变量显式设置时该字段被锁定：GET 里 locked=true，PUT 返回 409 setting_locked。
3. 数值/类型/方案名在入口处校验，非法输入 422 且不落盘。
4. settings.json 损坏时回退默认，不能让整条判定链路不可用。
5. provider 是可换的：注册表里两个方案的分数量纲不同，换方案必须标 stale。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import config, media_routes, review
from app.main import app
from app.store import Store


def _client():
    return TestClient(app, base_url="http://127.0.0.1:8765")


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    root = tmp_path / "wx"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS",
                        ({"id": "articles-full", "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    monkeypatch.setattr(media_routes, "STORE", store)
    return store, root


def test_get_returns_defaults_presets_and_providers(setup):
    body = _client().get("/api/settings/nsfw").json()

    assert body["threshold"] == pytest.approx(0.5)
    assert body["auto_scan"] is True
    assert body["provider"] == "falconsai-vit-b16"
    assert body["locked"] == {"threshold": False, "auto_scan": False}
    assert [p["id"] for p in body["presets"]] == ["loose", "standard", "strict"]
    assert [p["threshold"] for p in body["presets"]] == [0.65, 0.5, 0.35]
    ids = {p["id"] for p in body["providers"]}
    assert ids == {"falconsai-vit-b16", "nsfwjs-mobilenet-v2"}
    assert body["stale"] == {"threshold": False, "provider": False, "pending": 0}


def test_put_threshold_persists_and_marks_stale(setup):
    client = _client()
    resp = client.put("/api/settings/nsfw", json={"threshold": 0.65})

    assert resp.status_code == 200
    body = resp.json()
    assert body["threshold"] == pytest.approx(0.65)
    assert body["stale"]["threshold"] is True
    assert client.get("/api/settings/nsfw").json()["threshold"] == pytest.approx(0.65)
    # 落盘位置是 DATA_DIR/settings.json（conftest 已把它指向 tmp）
    assert json.loads(config.settings_path().read_text(encoding="utf-8"))["nsfw"]["threshold"] == pytest.approx(0.65)


def test_put_partial_update_keeps_other_fields(setup):
    client = _client()
    client.put("/api/settings/nsfw", json={"threshold": 0.65})
    body = client.put("/api/settings/nsfw", json={"auto_scan": False}).json()

    assert body["auto_scan"] is False
    assert body["threshold"] == pytest.approx(0.65)
    assert body["provider"] == "falconsai-vit-b16"


def test_put_switching_provider_marks_provider_stale(setup):
    body = _client().put("/api/settings/nsfw", json={"provider": "nsfwjs-mobilenet-v2"}).json()

    assert body["provider"] == "nsfwjs-mobilenet-v2"
    assert body["stale"]["provider"] is True


@pytest.mark.parametrize("payload", [
    {"threshold": 1.5},
    {"threshold": -0.1},
    {"threshold": "high"},
    {"threshold": True},
    {"auto_scan": "yes"},
    {"provider": ""},
    {"provider": "no-such-provider"},
    {"unknown_field": 1},
])
def test_put_rejects_invalid_payload(setup, payload):
    assert _client().put("/api/settings/nsfw", json=payload).status_code == 422


def test_put_empty_payload_is_rejected(setup):
    assert _client().put("/api/settings/nsfw", json={}).status_code == 422


def test_invalid_payload_does_not_touch_file(setup):
    client = _client()
    client.put("/api/settings/nsfw", json={"threshold": 0.65})
    before = config.settings_path().read_text(encoding="utf-8")
    client.put("/api/settings/nsfw", json={"threshold": 2.0})

    assert config.settings_path().read_text(encoding="utf-8") == before


def test_locked_field_returns_409_and_env_value_wins(setup, monkeypatch):
    client = _client()
    monkeypatch.setenv("AI_MEDIA_NSFW_THRESHOLD", "0.4")

    body = client.get("/api/settings/nsfw").json()
    assert body["locked"]["threshold"] is True
    assert body["threshold"] == pytest.approx(config.NSFW_THRESHOLD)

    resp = client.put("/api/settings/nsfw", json={"threshold": 0.65})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "setting_locked"


def test_auto_scan_lock_follows_env(setup, monkeypatch):
    # 常量与 env 在真实部署里是启动时一起定下的；测试要同时改两者，否则只验证了「锁定」
    # 而没验证「覆盖生效」（NSFW_SCAN 是 import 时固化的）。
    monkeypatch.setattr(config, "NSFW_SCAN", False)
    monkeypatch.setenv("AI_MEDIA_NSFW_SCAN", "0")
    body = _client().get("/api/settings/nsfw").json()

    assert body["locked"]["auto_scan"] is True
    assert body["auto_scan"] is False
    assert _client().put("/api/settings/nsfw", json={"auto_scan": True}).status_code == 409


def test_corrupt_settings_falls_back_to_defaults(setup):
    config.settings_path().write_text("{not json", encoding="utf-8")
    config._nsfw_settings_cache.update(mtime=None, data=None)

    body = _client().get("/api/settings/nsfw").json()
    assert body["threshold"] == pytest.approx(0.5)
    assert body["provider"] == "falconsai-vit-b16"


def test_write_is_atomic_and_leaves_no_temp_file(setup):
    _client().put("/api/settings/nsfw", json={"threshold": 0.35})
    leftovers = [p.name for p in config.settings_path().parent.iterdir()
                 if p.name.startswith("settings.json") and p.name != "settings.json"]

    assert leftovers == []


def test_installed_flag_tracks_weight_file(setup, tmp_path, monkeypatch):
    weight = tmp_path / "custom.onnx"
    monkeypatch.setattr(config, "NSFW_MODEL_PATH", weight)
    config._nsfw_settings_cache.update(mtime=None, data=None)
    before = {p["id"]: p["installed"] for p in _client().get("/api/settings/nsfw").json()["providers"]}
    assert before["falconsai-vit-b16"] is False

    weight.write_bytes(b"not-a-real-model")
    after = {p["id"]: p["installed"] for p in _client().get("/api/settings/nsfw").json()["providers"]}
    assert after["falconsai-vit-b16"] is True


def test_provider_spec_falls_back_for_unknown_name(setup):
    """settings.json 被手工写坏方案名时按默认方案继续跑，而不是整条链路不可用。"""
    config.settings_path().write_text(
        json.dumps({"nsfw": {"provider": "broken-provider"}}), encoding="utf-8")
    config._nsfw_settings_cache.update(mtime=None, data=None)

    assert review.current_provider() == "falconsai-vit-b16"
    assert review.provider_spec()["model_version"] == "Falconsai/nsfw_image_detection"
