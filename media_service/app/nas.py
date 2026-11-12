"""把归档目录推到极空间 NAS（走官方 zspace-cli，cloud 代理 + keychain 凭证）。

两条设计约束：
- **只新增不覆盖**：远端已有同名文件时 CLI 返回 `E_EXISTS`，这里当成「跳过」，
  所以重复执行是安全的，也不会改坏上一次传上去的东西。
- **失败不外抛**：调用方（jobs）捕获后写进 meta，抓好的文章不因 NAS 抖动而丢。

凭证不在代码里：CLI 自己从 macOS keychain 取 OAuth token。
"""
from __future__ import annotations

import argparse
import json
import logging
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger("wx.nas")

Runner = Callable[[list[str]], dict]


def file_info(path: str, *, runner: Runner | None = None) -> dict:
    """读取远端元信息；查询失败不能解释成目标不存在。"""
    invoke = runner or _run_cli
    params = json.dumps({"path": path}, ensure_ascii=False)
    data = invoke(["file", "info", "--params", params])
    return data if isinstance(data, dict) else {}


def download_bytes(path: str, *, runner: Runner | None = None) -> bytes:
    """通过独立临时目录下载远端原始字节，并确保临时文件清理。"""
    invoke = runner or _run_cli
    with tempfile.TemporaryDirectory(prefix="ai-media-nas-") as directory:
        root = Path(directory)
        params = json.dumps({"path": path, "local_path": "payload"}, ensure_ascii=False)
        invoke(["--local-root", str(root), "file", "download", "--params", params, "--yes"])
        return (root / "payload").read_bytes()


class NasError(RuntimeError):
    """CLI 返回 ok:false，或进程本身起不来。"""


def cli_path() -> str:
    """定位 zspace-cli：配置里的绝对路径优先，其次 PATH。"""
    cand = Path(config.NAS_CLI).expanduser()
    if cand.is_file():
        return str(cand)
    on_path = shutil.which("zspace-cli")
    if on_path:
        return on_path
    raise NasError(f"找不到 zspace-cli（{cand} 不存在，PATH 里也没有）")


def _parse(text: str) -> dict | None:
    """CLI 成功往 stdout 打 JSON、失败往 stderr 打 JSON，两种都要认。"""
    text = (text or "").strip()
    if not text:
        return None
    candidates = [text, *[ln for ln in reversed(text.splitlines()) if ln.lstrip().startswith("{")]]
    for chunk in candidates:
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _run_cli(args: list[str]) -> dict:
    """跑一次 CLI，返回 data；ok:false 或输出不可解析都抛 NasError。"""
    cmd = [cli_path(), "--profile", config.NAS_PROFILE, "--timeout", str(config.NAS_TIMEOUT), *args]
    log.debug("zspace-cli %s", " ".join(args))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=config.NAS_TIMEOUT + 30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NasError(f"调用 zspace-cli 失败：{exc}") from exc
    obj = _parse(proc.stdout) or _parse(proc.stderr)
    if obj is None:
        raw = ((proc.stdout or "") + (proc.stderr or ""))[:300]
        raise NasError(f"zspace-cli 输出无法解析（exit={proc.returncode}）：{raw}")
    if not obj.get("ok"):
        err = obj.get("error") or {}
        raise NasError(f"{err.get('code') or 'E_UNKNOWN'}: {err.get('message') or '未知错误'}")
    return obj.get("data") or {}


def split_path(path: str) -> tuple[str, str]:
    """`/a/b/c` → (`/a/b`, `c`)：file mkdir 要的是 parent + name 两段。"""
    cleaned = (path or "").strip().rstrip("/")
    parent, _, name = cleaned.rpartition("/")
    return parent or "/", name


# 远端目录名替换表：`file upload` 解析父目录时把名字里的 '+' 当成 query 里的空格
# （实测父目录名带 '+' 时报 N001320 父文件不存在；文件名带 '+'、mkdir 都正常），
# 而 CLI 又拒收 %2B 这类编码路径。统一换成同形的全角字符：看着一样，但不带 URL 语义。
_SAFE = str.maketrans({"+": "＋", "%": "％", "#": "＃", "?": "？", "&": "＆", "/": "／", "\\": "＼"})


def remote_name(name: str) -> str:
    """本地文章目录名 → NAS 目录名：只换危险字符，不改可见文字。"""
    safe = (name or "").translate(_SAFE).strip()
    if safe in ("", ".", ".."):
        return "未命名"
    return safe


def ensure_dir(path: str, *, runner: Runner = _run_cli) -> bool:
    """确保 NAS 上 path 存在；新建返回 True，本来就有返回 False。"""
    parent, name = split_path(path)
    if not name:
        raise NasError(f"目标目录不合法：{path!r}")
    params = json.dumps({"parent": parent, "name": name}, ensure_ascii=False)
    try:
        runner(["file", "mkdir", "--params", params, "--yes"])
        return True
    except NasError as exc:
        if "E_EXISTS" in str(exc):
            return False
        raise


def upload_file(local: Path, dest: str, *, local_root: Path, runner: Runner = _run_cli) -> str:
    """上传单个文件；远端已有同名文件返回 'skipped'，正常传上去返回 'uploaded'。"""
    params = json.dumps(
        {"local_path": str(local.relative_to(local_root)), "dest_path": dest}, ensure_ascii=False
    )
    try:
        runner(["--local-root", str(local_root), "file", "upload", "--params", params, "--yes"])
        return "uploaded"
    except NasError as exc:
        if "E_EXISTS" in str(exc):
            return "skipped"
        raise


def upload_article(local_dir: Path, remote_root: str | None = None, *, runner: Runner = _run_cli) -> dict:
    """把一篇文章目录整体推上去（含 meta.json 与 _rejected/）。

    远端结构：`<remote_root>/<文章标题>/<原文件名>`，子目录（`_rejected`）原样保留。
    """
    root = (remote_root or config.NAS_ARCHIVE_ROOT).strip().rstrip("/")
    local_dir = Path(local_dir)
    target = remote_name(local_dir.name)          # '+' 等字符换成同形全角，否则上传必失败
    remote_dir = f"{root}/{target}"
    files = sorted(p for p in local_dir.rglob("*") if p.is_file())
    result: dict = {
        "remote": remote_dir, "remote_name": target, "renamed": target != local_dir.name,
        "uploaded": 0, "skipped": 0, "failed": [], "dirs": 0,
    }
    if not files:
        result["failed"].append(("*", "本地目录没有文件"))
        return result

    if ensure_dir(root, runner=runner):
        result["dirs"] += 1
    if ensure_dir(remote_dir, runner=runner):
        result["dirs"] += 1
    subdirs = sorted(
        {p.parent.relative_to(local_dir).as_posix() for p in files} - {"."},
        key=lambda s: s.count("/"),
    )
    for sub in subdirs:
        if ensure_dir(f"{remote_dir}/{sub}", runner=runner):
            result["dirs"] += 1

    for path in files:
        rel_dir = path.parent.relative_to(local_dir).as_posix()
        dest = f"{remote_dir}/{path.name}" if rel_dir == "." else f"{remote_dir}/{rel_dir}/{path.name}"
        try:
            outcome = upload_file(path, dest, local_root=local_dir, runner=runner)
        except NasError as exc:
            result["failed"].append((f"{rel_dir}/{path.name}", str(exc)))
            continue
        result[outcome] += 1
    return result


def upload_tree(images_root: Path, remote_root: str | None = None, *, runner: Runner = _run_cli) -> dict:
    """批量：images_root 下每个子目录当作一篇文章。"""
    images_root = Path(images_root)
    summary: dict = {"articles": 0, "uploaded": 0, "skipped": 0, "failed": [], "results": []}
    for d in sorted(p for p in images_root.iterdir() if p.is_dir()):
        try:
            one = upload_article(d, remote_root, runner=runner)
        except NasError as exc:
            summary["failed"].append((d.name, str(exc)))
            continue
        summary["articles"] += 1
        summary["uploaded"] += one["uploaded"]
        summary["skipped"] += one["skipped"]
        summary["failed"] += [(f"{d.name}/{n}", e) for n, e in one["failed"]]
        summary["results"].append(one)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.nas", description="把 wx-archiver 抓下来的文章图片推到极空间 NAS"
    )
    ap.add_argument("path", help="一篇文章目录，或放多篇文章的父目录（如 data/jobs/<id>/images）")
    ap.add_argument("--root", default=None, help=f"NAS 目标根目录，默认 {config.NAS_ARCHIVE_ROOT}")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要做的事，不真传")
    args = ap.parse_args(argv)

    path = Path(args.path).expanduser().resolve()
    if not path.is_dir():
        print(f"不是目录：{path}")
        return 2
    root = args.root or config.NAS_ARCHIVE_ROOT
    is_article = (path / "meta.json").exists() or bool(list(path.glob("*.png")))

    if args.dry_run:
        targets = [path] if is_article else [d for d in sorted(path.iterdir()) if d.is_dir()]
        for t in targets:
            n = sum(1 for _ in t.rglob("*") if _.is_file())
            print(f"would upload {t} （{n} 个文件）→ {root}/{t.name}")
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = upload_article(path, root) if is_article else upload_tree(path, root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
