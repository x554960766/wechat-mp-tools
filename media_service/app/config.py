"""运行期配置：路径、浏览器通道、风控限速。"""
from __future__ import annotations

import os
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.environ.get("AI_MEDIA_DATA_DIR", ROOT / "data"))
PROFILE_DIR = Path(os.environ.get("AI_MEDIA_PROFILE_DIR", DATA_DIR / "chrome-profile"))
INDEX_DB = DATA_DIR / "index.sqlite"
THUMB_DIR = DATA_DIR / "thumbs"
WX_PORT = int(os.environ.get("WX_PORT", "8765"))
MODEL_ID = "OFA-Sys/chinese-clip-vit-base-patch16"
MODEL_REVISION = os.environ.get("AI_MEDIA_MODEL_REVISION", "36e679e65c2a2fead755ae21162091293ad37834")
MAX_CACHE_BYTES = 5 * 1024**3
MAX_PENDING_JOBS = 100
MIN_FREE_BYTES = 512 * 1024**2
MAX_MODEL_WAITING = 16
MAX_UPLOAD_BYTES = 30 * 1024**2
MAX_BODY_BYTES = 32 * 1024**2
MAX_ZIP_TEXT_BYTES = 100 * 1024**2
MAX_ZIP_ENTRIES = 1000
MAX_URLS_PER_JOB = 500
MAX_IMAGE_PIXELS = 40_000_000

# 用本机真实 Chrome 而不是 Playwright 自带 Chromium：指纹更像人，风控更宽松
CHANNEL = os.environ.get("WX_CHANNEL", "chrome")
# 无头模式在这台机器上会被验证码墙拦下，默认必须有头
HEADLESS = os.environ.get("WX_HEADLESS", "0") == "1"

# 风控节奏：连续抓取过快会触发「环境异常」
MIN_DELAY = float(os.environ.get("WX_MIN_DELAY", "4"))
MAX_DELAY = float(os.environ.get("WX_MAX_DELAY", "8"))
PAGE_TIMEOUT_MS = int(os.environ.get("WX_PAGE_TIMEOUT_MS", "45000"))
# 撞到环境验证后等待放行再重试（实测 60s 左右风控窗口会过去）
VERIFY_WAIT = int(os.environ.get("WX_VERIFY_WAIT", "60"))
VERIFY_RETRY = int(os.environ.get("WX_VERIFY_RETRY", "2"))
# 连续命中会升级封禁：等待时间要翻倍，固定 60s 实测不够
VERIFY_BACKOFF = float(os.environ.get("WX_VERIFY_BACKOFF", "2"))
# 静默降级（poc_token 页只给文字不给图）要等风控窗口退去，实测以分钟计
DEGRADED_WAIT = int(os.environ.get("WX_DEGRADED_WAIT", "120"))
SCROLL_ROUNDS = int(os.environ.get("WX_SCROLL_ROUNDS", "8"))

# 小于该体积的 gif 视为表情包，跳过
EMOJI_MAX_BYTES = 8 * 1024
# 单图上限，防异常大文件
MAX_IMAGE_BYTES = 30 * 1024 * 1024

# —— 二期：图片过滤口径（保守档，宁放过多余图，不误杀正文配图）——
# 短边下限：正文小图标/分割装饰通常 < 200px；实测最矮的正经图是 383px
FILTER_MIN_SIDE = int(os.environ.get("WX_FILTER_MIN_SIDE", "200"))
# 面积下限：挡住「宽 1000 高 20」这类长条，同时放过 897x383 的窄封面
FILTER_MIN_AREA = int(os.environ.get("WX_FILTER_MIN_AREA", "80000"))
# 长宽比上限：超过即视为分割线/横条
FILTER_MAX_ASPECT = float(os.environ.get("WX_FILTER_MAX_ASPECT", "12"))
# 灰度标准差低于该值视为纯色占位图
FILTER_MIN_STD = float(os.environ.get("WX_FILTER_MIN_STD", "4.0"))
# 透明像素占比超过该值视为近乎全透明（水印层、空白蒙版）
FILTER_MAX_TRANSPARENT = float(os.environ.get("WX_FILTER_MAX_TRANSPARENT", "0.9"))
# 同篇文章内 dHash 汉明距离 ≤ 该值视为重复图（64 位，≤6 仍是很严的重复判定）
FILTER_DEDUPE_DISTANCE = int(os.environ.get("WX_FILTER_DEDUPE_DISTANCE", "6"))

# —— 二期：上传到极空间 NAS（走官方 zspace-cli）——
# 抓完一篇、过滤完就自动推上去；关掉用 WX_NAS_UPLOAD=0（本地照常归档）
NAS_UPLOAD = os.environ.get("WX_NAS_UPLOAD", "1") not in ("0", "false", "False", "no")
# 官方 CLI：技能包自带的 thin 包，首次调用会自行下载固定版本；不存在时回退到 PATH
NAS_CLI = os.environ.get(
    "WX_NAS_CLI",
    os.path.expanduser(
        "~/Library/Application Support/dsh-desktop/harness/skills/"
        "zspace-nas-agent-toolkit/scripts/zspace-cli"
    ),
)
# CLI 登录 profile（OAuth 凭证存在 macOS keychain，代码里不放 token）
NAS_PROFILE = os.environ.get("WX_NAS_PROFILE", "default")
# 远端归档根目录：file space 实测个人空间真实路径是 /sata11/my/data
NAS_ARCHIVE_ROOT = os.environ.get("WX_NAS_ROOT", "/sata11/my/data/公众号归档")
# 单次 CLI 调用超时秒数（CLI 允许 2~300）
NAS_TIMEOUT = int(os.environ.get("WX_NAS_TIMEOUT", "120"))

# —— 旁挂 wechat-mp-tools（外部 HTTP 采集源）——
# 旁挂是一个独立的 Flask 进程（python app_mp.py --port 5200），自己持有微信读书
# 登录态、逆向签名与验证码流程。本项目只把它当只读 HTTP 源用，链路在 app/mp_source.py。
MP_TOOLS_BASE = os.environ.get("MP_TOOLS_BASE", "http://127.0.0.1:5200")
# 旁挂调用超时（秒）：超时即判 unavailable，由调用方决定是否退避重试。
MP_TOOLS_TIMEOUT = float(os.environ.get("MP_TOOLS_TIMEOUT", "15"))


def _media_roots() -> tuple[dict, ...]:
    configured = os.environ.get("AI_MEDIA_ROOTS")
    if configured:
        roots = json.loads(configured)
    else:
        roots = [
            {"id": "wx-archive", "type": "zspace", "path": NAS_ARCHIVE_ROOT, "writable": True},
            {"id": "iphone", "type": "local", "path": os.environ.get(
                "AI_MEDIA_IPHONE_ROOT", "/Volumes/sata11-158XXXX6829/iphone"
            ), "writable": False},
            {"id": "volume-archive", "type": "local", "path": os.environ.get(
                "AI_MEDIA_VOLUME_ROOT", "/Volumes/sata11-158XXXX6829"
            ), "writable": False, "exclude_paths": ["iphone", "my"]},
        ]
    if not isinstance(roots, list) or not roots:
        raise ValueError("AI_MEDIA_ROOTS 必须是非空 JSON 数组")
    ids: set[str] = set()
    for item in roots:
        if not isinstance(item, dict) or not all(k in item for k in ("id", "type", "path")):
            raise ValueError("媒体根需要 id/type/path")
        if item["id"] in ids or item["type"] not in ("local", "zspace"):
            raise ValueError("媒体根 id 重复或 type 无效")
        if not isinstance(item["path"], str) or not item["path"].startswith("/"):
            raise ValueError("媒体根 path 必须为绝对路径")
        excludes = item.get("exclude_paths", [])
        if not isinstance(excludes, list) or any(
            not isinstance(path, str) or not path or path.startswith("/") or "\\" in path or
            any(part in ("", ".", "..") for part in path.split("/")) for path in excludes
        ):
            raise ValueError("exclude_paths 必须是根内安全的相对路径数组")
        ids.add(item["id"])
    return tuple(roots)


MEDIA_ROOTS = _media_roots()


def get_root(root_id: str) -> dict:
    for item in MEDIA_ROOTS:
        if item["id"] == root_id:
            return item
    raise KeyError(f"未知媒体根：{root_id}")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
