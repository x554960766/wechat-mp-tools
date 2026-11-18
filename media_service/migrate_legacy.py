"""旧图片索引迁移命令：先出预案，再落库。

三个来源项目（wx-archiver、nas-image-search、zspace-photo-search）合并时留下一份旧索引
（约 13,043 条），``media_service.app.store.import_legacy`` 已经能把它们搬进统一索引，但
**没有任何入口调用它**：没有 CLI，也没有端点。后果是运维只能自己拼 Python 片段去调，
既不知道要跑多久，也不知道有多少条会失败。

本命令补上这个入口，并坚持「先看后动」：

- 默认 ``--dry-run``：只读旧库与目标索引，报「多少条能进来、多少条路径越界、多少条文件已不在、
  多少条已经存在」，零写入、零模型推理;
- ``--apply`` 才真正调用 ``store.import_legacy`` 落库（单事务、已有键不覆盖、幂等）;
- 根映射默认取当前配置的媒体根（旧路径 == 新根路径），``--map 旧路径=根ID`` 可追加或覆盖，
  旧卷换挂载点时就靠它。

为什么预案不复用 ``import_legacy`` 内部循环：那个函数必须读文件字节并跑 CLIP 编码才算完成，
而预演的价值恰恰在于**不碰文件、不加载模型**。代价是分类逻辑有两份，因此
``tests/test_migrate_legacy.py`` 里有一条测试直接比对「预案数 == 实际导入报告数」，
两份一旦走偏就红。

用法::

    media_service/.venv/bin/python -m media_service.migrate_legacy
    media_service/.venv/bin/python -m media_service.migrate_legacy --json
    media_service/.venv/bin/python -m media_service.migrate_legacy \\
        --map /Volumes/sata11-158XXXX6829=/volumes-sata11 --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from media_service.app import config

DEFAULT_SOURCE = Path("/Users/yaobinsun/IdeaProjects/ai-media/data/media/legacy-import/index.sqlite")
SAMPLE_LIMIT = 5


def _default_mapping() -> dict[str, str]:
    """当前配置的媒体根天然就是「旧路径 → 根 ID」的映射。"""
    return {item["path"]: item["id"] for item in config.MEDIA_ROOTS}


def _merge_mapping(pairs: list[str]) -> dict[str, str]:
    mapping = _default_mapping()
    for pair in pairs or []:
        old, sep, new = pair.partition("=")
        if not sep or not old or not new:
            raise ValueError(f"--map 需要 旧路径=根ID 的形式：{pair}")
        if not old.startswith("/"):
            raise ValueError(f"--map 的旧路径必须是绝对路径：{old}")
        try:
            config.get_root(new)
        except KeyError as exc:
            raise ValueError(str(exc.args[0])) from None
        mapping[old] = new
    return mapping


def _open_source(source_db: Path) -> sqlite3.Connection:
    source = Path(source_db).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"旧索引不存在：{source}")
    conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def plan_legacy(source_db: Path | str, root_mapping: dict[str, str], *, store=None) -> dict:
    """只读推演 import_legacy 会怎么走，返回可交付的预案。

    分类口径与 ``store.import_legacy`` 保持一致，多出来的 ``missing_file`` /
    ``size_mismatch`` / ``invalid_relpath`` 是拆细项，不是新判定：前三者最终都会计入
    ``invalid``（旧函数把 OSError 与校验失败一律算 invalid），第四项是「文件在、大小变了」，
    真实导入仍会重编码并成功，因此它属于提示而非失败。
    """
    from media_service.app import media
    from media_service.app.store import STORE

    store = store or STORE
    mappings = sorted(
        ((Path(old).resolve(), target) for old, target in root_mapping.items()),
        key=lambda item: len(str(item[0])),
        reverse=True,
    )
    seen: set[tuple[str, str]] = set()
    report = {
        "rows": 0,
        "mapped": 0,
        "ready": 0,
        "existing": 0,
        "skipped_unmapped": 0,
        "invalid": 0,
        "invalid_path": 0,
        "invalid_relpath": 0,
        "missing_file": 0,
        "size_mismatch": 0,
        "by_root": {},
        "by_root_mapped": {},
        "samples": {"unmapped": [], "missing": []},
    }
    with _open_source(source_db) as src:
        tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "images" not in tables:
            raise ValueError("旧库缺少 images 表")
        columns = {row[1] for row in src.execute("PRAGMA table_info(images)")}
        if not {"path", "mtime", "size"} <= columns:
            raise ValueError("旧库缺少 path/mtime/size 字段")
        rows = src.execute("SELECT path,mtime,size FROM images").fetchall()

    for row in rows:
        report["rows"] += 1
        raw = Path(str(row["path"]))
        if not raw.is_absolute():
            report["invalid"] += 1
            report["invalid_path"] += 1
            continue
        possible = next(
            ((base, target) for base, target in mappings if raw.is_relative_to(base)), None
        )
        if not possible:
            report["skipped_unmapped"] += 1
            if len(report["samples"]["unmapped"]) < SAMPLE_LIMIT:
                report["samples"]["unmapped"].append(str(raw))
            continue
        report["mapped"] += 1
        base, target_root = possible
        report["by_root_mapped"][target_root] = report["by_root_mapped"].get(target_root, 0) + 1
        resolved = raw.resolve()
        if not resolved.is_relative_to(base):
            report["invalid"] += 1
            report["invalid_path"] += 1
            continue
        try:
            relpath = resolved.relative_to(base).as_posix()
            media.validate_relpath(relpath)
        except ValueError:
            report["invalid"] += 1
            report["invalid_relpath"] += 1
            continue
        with store.lock:
            hit = store.conn.execute(
                "SELECT 1 FROM images WHERE root=? AND relpath=?", (target_root, relpath)
            ).fetchone()
        if hit or (target_root, relpath) in seen:
            report["existing"] += 1
            continue
        if not resolved.is_file():
            report["invalid"] += 1
            report["missing_file"] += 1
            if len(report["samples"]["missing"]) < SAMPLE_LIMIT:
                report["samples"]["missing"].append(f"{target_root}/{relpath}")
            continue
        try:
            if resolved.stat().st_size != int(row["size"] or 0):
                report["size_mismatch"] += 1
        except OSError:
            report["invalid"] += 1
            report["missing_file"] += 1
            continue
        seen.add((target_root, relpath))
        report["ready"] += 1
        report["by_root"][target_root] = report["by_root"].get(target_root, 0) + 1
    return report


def _print_report(report: dict, mapping: dict[str, str], *, dry_run: bool) -> None:
    print(f"模式：{'dry-run（未写入任何数据）' if dry_run else 'apply（已落库）'}")
    print("根映射：" + "，".join(f"{old} → {new}" for old, new in sorted(mapping.items())))
    print(f"旧库总行数：{report['rows']}")
    if dry_run:
        print(f"  可导入（文件在、未存在）：{report['ready']}")
        print(f"  已存在（幂等跳过）：{report['existing']}")
        print(f"  未映射根（旧卷没挂载？）：{report['skipped_unmapped']}")
        print(f"  不可用合计：{report['invalid']}"
              f"（路径越界 {report['invalid_path']}，相对路径非法 {report['invalid_relpath']}，"
              f"文件已不在 {report['missing_file']}）")
        print(f"  大小与旧库不符（仍会重编码导入）：{report['size_mismatch']}")
        for root, count in sorted(report["by_root_mapped"].items()):
            print(f"  命中根 {root}：{count} 行（其中可立即导入 {report['by_root'].get(root, 0)}）")
        for label, paths in report["samples"].items():
            if paths:
                print(f"  样例（{label}）：" + "，".join(paths))
    else:
        print(f"  新插入：{report.get('inserted', 0)}")
        print(f"  已存在：{report.get('existing', 0)}")
        print(f"  未映射：{report.get('skipped', 0)}")
        print(f"  不可用：{report.get('invalid', 0)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="旧图片索引迁移：默认只出预案，--apply 才落库")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="旧索引 sqlite 路径")
    parser.add_argument(
        "--map", action="append", metavar="旧路径=根ID",
        help="覆盖或追加根映射，可重复；默认取当前媒体根配置",
    )
    parser.add_argument("--apply", action="store_true", help="真正写入统一索引（默认 dry-run）")
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于存档或二次处理")
    args = parser.parse_args(argv)

    try:
        mapping = _merge_mapping(args.map)
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2
    if not mapping:
        print("没有可用根映射：请用 --map 指定 旧路径=根ID", file=sys.stderr)
        return 2

    try:
        if args.apply:
            from media_service.app.store import import_legacy

            report = import_legacy(args.source, mapping)
        else:
            report = plan_legacy(args.source, mapping)
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"迁移中止：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"dry_run": not args.apply, "mapping": mapping, "report": report},
                         ensure_ascii=False, indent=2))
    else:
        _print_report(report, mapping, dry_run=not args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
