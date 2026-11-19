"""测试级隔离：settings.json 不落进仓库内的 data/。

默认 DATA_DIR 是仓库里的 media_service/data，源目录内的配置一旦被测试写脏，
后续测试会读到上一轮的阈值/方案（更糟的是它会被当成「用户配置」而盖住
monkeypatch 的常量），表现为随机失败。这里只把 settings_path 指向 tmp，
不动 DATA_DIR——jobs/archive 等仍按原路径工作。
"""
from __future__ import annotations

import pytest

from app import config


@pytest.fixture(autouse=True)
def _isolated_nsfw_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "settings_path", lambda: path)
    config._nsfw_settings_cache.update(mtime=None, data=None)
    yield
    config._nsfw_settings_cache.update(mtime=None, data=None)
