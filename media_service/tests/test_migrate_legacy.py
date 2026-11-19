"""迁移命令的单元测试：全 mock，不碰真实旧库与模型。

关键红线一条：**plan 的「可导入」数必须与实际导入报告的「inserted」数一致**。
plan 与 ``store.import_legacy`` 的分类逻辑是两份独立实现（plan 不读文件、不跑模型，
import_legacy 必须 read_bytes + encode），这条测试就是让两份逻辑不敢走偏的约束——
写在注释里会被后来者越过，写在测试里不会。
"""
from __future__ import annotations

import sqlite3
import sys
from io import BytesIO
from pathlib import Path

# 测试以 media_service 为 cwd 运行；补上仓库根，才能按包名导入命令行模块。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PIL import Image
import numpy as np

from media_service import migrate_legacy
from media_service.app import config, media, encoder
from media_service.app.store import Store, import_legacy


def _png(color) -> bytes:
    out = BytesIO()
    Image.new("RGB", (8, 8), color).save(out, "PNG")
    return out.getvalue()


def _setup(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(config, "MEDIA_ROOTS", ({"id": "r", "type": "local", "path": str(root), "writable": True},))
    store = Store(tmp_path / "index.sqlite")
    content = {
        "pic/one.png": _png("red"),
        "pic/two.png": _png("green"),
        "pic/gone.png": _png("blue"),
    }
    # 只把 one/two 落到磁盘：gone.png 故意缺席，用来验证「文件已不在」分类。
    (root / "pic").mkdir(parents=True, exist_ok=True)
    for name in ("pic/one.png", "pic/two.png"):
        (root / name).write_bytes(content[name])
    def _read(root_id, path):
        # gone.png 在旧库里有记录、磁盘上已不存在：读字节必须抛 OSError，
        # 否则 mock 会绕过真实存在的「文件已不在」分支，plan 与导入就对不上了。
        if path not in content or not (root / path).is_file():
            raise OSError(f"文件不存在：{path}")
        return content[path]

    monkeypatch.setattr(media, "read_bytes", _read)
    monkeypatch.setattr(encoder, "encode_image", lambda data: np.eye(1, 512, 0, dtype=np.float32)[0])
    source = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE images(path TEXT, mtime REAL, size INTEGER)")
    conn.executemany(
        "INSERT INTO images VALUES(?,?,?)",
        [
            (f"{root}/pic/one.png", 1.0, len(content["pic/one.png"])),
            (f"{root}/pic/two.png", 2.0, 999),  # 大小与现状不符 → size_mismatch，仍会导入
            (f"{root}/pic/gone.png", 3.0, 10),  # 文件不在 → missing_file / invalid
            (f"{root}/pic/../pic/one.png", 4.0, 10),  # resolve 后与首行同键 → 旧库内部重复
            ("rel/path.png", 5.0, 10),  # 相对路径 → invalid
            (f"/other/volume/x.png", 6.0, 10),  # 未映射根 → skipped
        ],
    )
    conn.commit()
    conn.close()
    return store, source, content


def test_plan_categories_match_import_semantics(monkeypatch, tmp_path):
    store, source, _ = _setup(monkeypatch, tmp_path)
    mapping = {str(tmp_path / "media"): "r"}
    plan = migrate_legacy.plan_legacy(source, mapping, store=store)
    assert plan["rows"] == 6
    assert plan["ready"] == 2          # one.png + two.png（文件在、未存在）
    assert plan["existing"] == 1       # pic/../pic/one.png 解析后与首行同键，算「已存在」
    assert plan["skipped_unmapped"] == 1
    assert plan["invalid"] == 2        # 相对路径 + 文件已不在
    assert plan["missing_file"] == 1
    assert plan["size_mismatch"] == 1  # two.png 大小与旧库不符，但仍会重编码导入
    assert plan["by_root"] == {"r": 2}


def test_plan_matches_actual_import(monkeypatch, tmp_path):
    """plan 说能进几条，真实导入就必须插几条——两份逻辑的共同契约。"""
    store, source, _ = _setup(monkeypatch, tmp_path)
    mapping = {str(tmp_path / "media"): "r"}
    plan = migrate_legacy.plan_legacy(source, mapping, store=store)
    report = import_legacy(source, mapping, store=store)
    assert plan["ready"] == report["inserted"]
    assert plan["existing"] == report["existing"]
    assert plan["invalid"] + plan["skipped_unmapped"] == report["invalid"] + report["skipped"]


def test_apply_is_idempotent_and_plan_turns_green(monkeypatch, tmp_path):
    store, source, _ = _setup(monkeypatch, tmp_path)
    mapping = {str(tmp_path / "media"): "r"}
    first = import_legacy(source, mapping, store=store)
    assert first["inserted"] == 2
    plan_after = migrate_legacy.plan_legacy(source, mapping, store=store)
    # 3 条：one.png、two.png，以及解析后与 one.png 同键的重复行——都已存在于目标索引。
    assert plan_after["existing"] == 3 and plan_after["ready"] == 0
    second = import_legacy(source, mapping, store=store)
    assert second["inserted"] == 0 and second["existing"] == 3


def test_cli_json_and_bad_args(monkeypatch, tmp_path, capsys):
    store, source, _ = _setup(monkeypatch, tmp_path)
    # plan_legacy 在调用时才 from ... import STORE，所以改模块属性即生效。
    monkeypatch.setattr("media_service.app.store.STORE", store)
    rc = migrate_legacy.main(["--source", str(source), "--map", f"{tmp_path / 'media'}=r", "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and '"rows": 6' in out and '"ready": 2' in out
    rc = migrate_legacy.main(["--source", str(source), "--map", f"{tmp_path / 'media'}=nope"])
    assert rc == 2
    rc = migrate_legacy.main(["--source", str(tmp_path / "none.sqlite")])
    assert rc == 1
