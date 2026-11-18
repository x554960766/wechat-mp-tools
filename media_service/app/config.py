"""运行期配置：路径、浏览器通道、风控限速。"""
from __future__ import annotations

import os
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.environ.get("AI_MEDIA_DATA_DIR", ROOT / "data"))
PROFILE_DIR = Path(os.environ.get("AI_MEDIA_PROFILE_DIR", DATA_DIR / "chrome-profile"))
INDEX_DB = DATA_DIR / "index.sqlite"
THUMB_DIR = DATA_DIR / "thumbs"

# NSFW 审查权重。与索引共用运行期 DATA_DIR（宿主 media_host 注入
# AI_MEDIA_DATA_DIR=<repo>/data/media），故默认落在 <DATA_DIR>/nsfw.onnx。
# 来源/许可/校验值（2026-11-13 核实，SHA-256 用 shasum -a 256 与 Python hashlib 双算一致）：
#   Falconsai/nsfw_image_detection，ViT-B/16 图像分类，Apache-2.0
#   输入 pixel_values [B,3,224,224] float，输出 logits [B,2]，id2label {0:normal, 1:nsfw}
NSFW_MODEL_SOURCE = "Falconsai/nsfw_image_detection"
NSFW_MODEL_LICENSE = "apache-2.0"
NSFW_MODEL_SHA256 = "d9afb1e057104e6cc8616d174f0f6a8b8b0389c839eea7d5efa4bdf1a77efd27"
NSFW_MODEL_PATH = Path(os.environ.get("AI_MEDIA_NSFW_MODEL", DATA_DIR / "nsfw.onnx"))
# 扫描期初筛开关。默认开：入库时顺手跑一遍，检索页才能直接带角标。
# 关掉只影响「是否写初筛结论」，不影响现有的按需审核路径。
NSFW_SCAN = os.environ.get("AI_MEDIA_NSFW_SCAN", "1").lower() not in ("0", "false", "no", "off")
# 判定阈值：review.py 判定时实时读取本值（唯一真源），扫描期与前端展示同此口径。
# 可用 AI_MEDIA_NSFW_THRESHOLD 覆盖：调小更敏感（更易判 nsfw），调大更宽松。
NSFW_THRESHOLD = float(os.environ.get("AI_MEDIA_NSFW_THRESHOLD", "0.5"))

# —— NSFW 可热更配置（<DATA_DIR>/settings.json 的 "nsfw" 段）——
# 上面几个常量仍是「部署级默认与覆盖」：AI_MEDIA_NSFW_THRESHOLD / AI_MEDIA_NSFW_SCAN
# 显式设置时对应字段锁定只读（UI 置灰、写入 409），有效值以环境变量为准；
# 未设置时读 settings.json，再缺就回退这里的默认值。provider（鉴黄方案）不设环境变量，
# 永远由 settings.json 决定。
# applied_*：最近一次「历史结论与当前配置对齐」时的口径（recompute 或 force 重扫后回写）。
# 与 threshold/provider 不一致即 stale——面板据此提示并给出两条对齐路径。
# 字段清单（写入白名单与未知字段校验的唯一来源）。
NSFW_SETTINGS_FIELDS = ("threshold", "auto_scan", "provider", "applied_threshold", "applied_provider", "applied_at")


def _nsfw_defaults() -> dict:
    """内置默认值。

    threshold/auto_scan 每次调用都读模块常量，而不是在 import 时固化一份副本：
    这两个常量是环境变量的落点，测试与部署都靠 monkeypatch/重启改它们，
    固化副本会让「改了常量但判定不跟」这种 bug 静默发生。
    """
    return {
        "threshold": NSFW_THRESHOLD,
        "auto_scan": NSFW_SCAN,
        "provider": "falconsai-vit-b16",
        "applied_threshold": NSFW_THRESHOLD,
        "applied_provider": "falconsai-vit-b16",
        "applied_at": None,
    }

_nsfw_settings_cache: dict = {"mtime": None, "data": None}


def settings_path() -> Path:
    return DATA_DIR / "settings.json"


def _read_settings_file() -> dict:
    """读取 settings.json（按 mtime 缓存）。

    缺失或损坏一律回退内置默认值并打一行告警：配置是扫描的附带输入，单点损坏
    不能让整条判定链路不可用。
    """
    path = settings_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        _nsfw_settings_cache.update(mtime=None, data=None)
        return {"nsfw": _nsfw_defaults()}
    if _nsfw_settings_cache["mtime"] == mtime and _nsfw_settings_cache["data"] is not None:
        return _nsfw_settings_cache["data"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("顶层必须是 JSON 对象")
        data = raw
    except Exception as exc:  # noqa: BLE001 - 配置损坏只降级，不抛
        print(f"[config] settings.json 解析失败，回退默认 NSFW 配置：{exc}")
        data = {"nsfw": _nsfw_defaults()}
    _nsfw_settings_cache.update(mtime=mtime, data=data)
    return data


def nsfw_settings() -> dict:
    """NSFW 有效配置：文件里的 nsfw 段（缺字段补默认），环境变量覆盖优先。"""
    stored = _nsfw_defaults()
    stored.update(_read_settings_file().get("nsfw") or {})
    if "AI_MEDIA_NSFW_THRESHOLD" in os.environ:
        stored["threshold"] = NSFW_THRESHOLD
    if "AI_MEDIA_NSFW_SCAN" in os.environ:
        stored["auto_scan"] = NSFW_SCAN
    return stored


def nsfw_locked_fields() -> dict:
    """哪些 NSFW 字段被部署级环境变量锁定（UI 只读、PUT 返回 409）。"""
    return {
        "threshold": "AI_MEDIA_NSFW_THRESHOLD" in os.environ,
        "auto_scan": "AI_MEDIA_NSFW_SCAN" in os.environ,
    }


def _atomic_write_settings(payload: dict) -> None:
    """临时文件 + os.replace 原子写；失败不留半截文件。"""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _nsfw_settings_cache.update(mtime=None, data=None)  # 立刻失效，下次读走文件


def save_nsfw_settings(patch: dict) -> dict:
    """校验并合并写入 settings.json 的 nsfw 段，返回保存后的完整段。

    只做类型/范围校验；provider 是否在注册表内由路由层结合 review.PROVIDERS 判断。
    失败抛 ValueError（调用方转 422），不做任何部分写入。
    """
    if not isinstance(patch, dict) or not patch:
        raise ValueError("至少要提供一个字段")
    allowed = set(NSFW_SETTINGS_FIELDS) - {"applied_threshold", "applied_provider", "applied_at"}
    unknown = set(patch) - allowed
    if unknown:
        raise ValueError(f"未知配置字段：{', '.join(sorted(unknown))}")
    # 基线取「文件值 + 默认值」，不取 nsfw_settings()：后者叠了环境变量覆盖，
    # 写回会把部署级取值固化进文件，env 撤掉后行为就悄悄变了。
    merged = _nsfw_defaults()
    merged.update(_read_settings_file().get("nsfw") or {})
    if "threshold" in patch:
        value = patch["threshold"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("threshold 必须是 0.0–1.0 的数字")
        value = float(value)
        if not 0.0 <= value <= 1.0:
            raise ValueError("threshold 必须在 0.0–1.0 之间")
        merged["threshold"] = value
    if "auto_scan" in patch:
        if not isinstance(patch["auto_scan"], bool):
            raise ValueError("auto_scan 必须是布尔值")
        merged["auto_scan"] = patch["auto_scan"]
    if "provider" in patch:
        if not isinstance(patch["provider"], str) or not patch["provider"].strip():
            raise ValueError("provider 必须是非空字符串")
        merged["provider"] = patch["provider"].strip()
    path = settings_path()
    raw = _read_settings_file() if path.is_file() else {"nsfw": _nsfw_defaults()}
    raw = dict(raw)
    raw["nsfw"] = merged
    _atomic_write_settings(raw)
    return merged


def mark_nsfw_applied(*, threshold: float | None = None, provider: str | None = None) -> None:
    """历史结论对齐完成后回写 applied_*（recompute 或 force 重扫结束时调用）。

    只改 applied_*，不动 threshold/provider——这些字段记录的是「历史结论现在
    用的口径」，与扫描结果对齐后写入；配置损坏时回退默认值即可，无需迁移。
    """
    settings = nsfw_settings()
    if threshold is not None:
        settings["applied_threshold"] = float(threshold)
    if provider is not None:
        settings["applied_provider"] = provider
    settings["applied_at"] = datetime.now(timezone.utc).isoformat()
    path = settings_path()
    raw = _read_settings_file() if path.is_file() else {"nsfw": _nsfw_defaults()}
    raw = dict(raw)
    raw["nsfw"] = settings
    _atomic_write_settings(raw)


# 视频代表帧：抽帧间隔与单视频上限。间隔越密越容易命中情节，但帧图与向量都会线性增长；
# 默认 10 秒 / 24 帧把一条 4 分钟视频压在 24 帧以内，足够覆盖一个短视频全片。
FRAME_INTERVAL = float(os.environ.get("AI_MEDIA_FRAME_INTERVAL", "10"))
FRAME_MAX = int(os.environ.get("AI_MEDIA_FRAME_MAX", "24"))
FRAMES_DIR = DATA_DIR / "frames"
WX_PORT = int(os.environ.get("WX_PORT", "8765"))
MODEL_ID = "OFA-Sys/chinese-clip-vit-base-patch16"
MODEL_REVISION = os.environ.get("AI_MEDIA_MODEL_REVISION", "36e679e65c2a2fead755ae21162091293ad37834")
MAX_CACHE_BYTES = 5 * 1024**3
MAX_PENDING_JOBS = 100
MIN_FREE_BYTES = 512 * 1024**2
MAX_MODEL_WAITING = 16
MAX_UPLOAD_BYTES = 30 * 1024**2
# 一次性 NSFW 检测允许上传视频，体积上限单独放宽；超过时在写盘阶段就中断，不进内存。
PROBE_UPLOAD_BYTES = int(os.environ.get("AI_MEDIA_PROBE_UPLOAD_MAX", str(1024**3)))
# 以图搜图支持直接贴图片网址：下载超时（秒），复用上传体积上限防超大文件
URL_DOWNLOAD_TIMEOUT = float(os.environ.get("AI_MEDIA_URL_TIMEOUT", "20"))
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
# 官方 CLI：技能包自带的 thin 包，首次调用会自行下载固定版本。
# 定位顺序固定：显式 WX_NAS_CLI → 共享技能真源 ~/.agents → 历史 DSH 技能路径 → PATH。
# 只在这里实现一次，nas.py 与 media.py 都读 NAS_CLI，避免两处 fallback 口径漂移。
_NAS_CLI_CANDIDATES = (
    os.path.expanduser("~/.agents/skills/zspace-nas-agent-toolkit/scripts/zspace-cli"),
    os.path.expanduser(
        "~/Library/Application Support/dsh-desktop/harness/skills/"
        "zspace-nas-agent-toolkit/scripts/zspace-cli"
    ),
)


def resolve_nas_cli() -> str:
    """返回可执行的 zspace-cli 路径；都找不到时返回最有希望的候选取值供报错。"""
    explicit = os.environ.get("WX_NAS_CLI")
    if explicit:
        return explicit
    for candidate in _NAS_CLI_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    on_path = shutil.which("zspace-cli")
    if on_path:
        return on_path
    return _NAS_CLI_CANDIDATES[0]


NAS_CLI = resolve_nas_cli()
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
