"""可选 NSFW 审查：多方案注册表 + 按需加载，不影响普通检索。

默认方案（falconsai）的口径与来源实现 nas-image-search/review.py 一致，并与官方
预处理逐项核对过：
- 预处理 resize N（bilinear）-> /255 -> (x-mean)/std -> NCHW
- 模型图内无 Softmax，必须调用方 softmax 后再取分数
- 阈值来自 config.nsfw_settings()["threshold"]（settings.json 可热更，env 可覆盖），
  只有 score >= 阈值判 nsfw（不能把 logit 当概率比阈值）

「鉴黄方案」在这里抽象成 PROVIDERS 注册表。三个方案的分数语义完全不同：
- falconsai-vit-b16：2 类 [normal, nsfw]，score = probs[1]
- nsfwjs-mobilenet-v2：5 类 [Drawing, Hentai, Neutral, Porn, Sexy]，
  score = probs[1]+probs[3]+probs[4]（三类露骨内容求和）
- freepik-eva02：4 档有序 [neutral, low, medium, high]，
  score = probs[2]+probs[3] = P(medium)+P(high)。刻意**不含 low**：实测 12 张
  真实缩略图中，紧身裙、白裙街拍这类「性感但未越线」的图会被模型判进 low 且
  low≈0.99，若把 low 计入，阈值 0.35 会把整篇文章的写真壁纸全部标黄。
  low 是「擦边」，medium/high 才是「露骨」，拦色情只取后两档。
所以换方案只能重扫，不能拿旧分数按新阈值重算——这是 Spec 定下的硬约束。

预处理差异全部落在 spec 字段里（input_size / interp / mean / std / layout /
softmax），review_bytes 只按字段执行、不写 if provider == ... 分支：三个方案的
插值方式（bilinear vs bicubic）、归一化（±1 vs CLIP mean/std vs 仅 /255）、
张量布局（NCHW vs NHWC）、输出是否已含 softmax 都不同，硬编码一种就会把另外
两种喂成垃圾分。

权重文件按 provider 分名放在 <DATA_DIR> 下（AI_MEDIA_NSFW_MODEL 只覆盖默认方案，
用于宿主指定自定义路径）。加载失败按「权重文件 mtime」记忆：文件被替换后自动重试，
避免一次坏文件就让进程内该方案永久不可用。
"""
from __future__ import annotations
from pathlib import Path
from . import config

DEFAULT_PROVIDER = "falconsai-vit-b16"

# CLIP ViT-B/16 的图像归一化常量（EVA02 基于同一套视觉编码器）。
_CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
_CLIP_STD = (0.26862954, 0.26130258, 0.27577711)

PROVIDERS = {
    "falconsai-vit-b16": {
        "label": "Falconsai ViT-B/16",
        "model_version": "Falconsai/nsfw_image_detection",
        "weight": "nsfw.onnx",
        "sha256": "d9afb1e057104e6cc8616d174f0f6a8b8b0389c839eea7d5efa4bdf1a77efd27",
        "input_size": 224,
        "interp": "bilinear",
        "mean": (0.5, 0.5, 0.5),
        "std": (0.5, 0.5, 0.5),
        "layout": "NCHW",
        "softmax": True,
        "score_index": 1,
        "nsfw_indices": None,
        "labels": ("normal", "nsfw"),
        "score_desc": "softmax 后的 nsfw 概率（2 类：normal / nsfw）",
    },
    "freepik-eva02": {
        "label": "Freepik EVA02",
        # 分数口径变更也进版本号：库里的老行是「1−neutral」算的，靠后缀区分，
        # 否则重扫到一半中断时，新旧两种口径的行在库里长得一模一样。
        "model_version": "Freepik/nsfw_image_detector@refs/pr/4+mh",
        "weight": "nsfw-freepik-eva02.onnx",
        "sha256": "3e4b381b77d0a711e1f61fdab973c3a04fccfff5697e074f465f4c49674e455d",
        "input_size": 448,
        "interp": "bicubic",
        "mean": _CLIP_MEAN,
        "std": _CLIP_STD,
        "layout": "NCHW",
        "softmax": True,
        "score_index": None,
        "nsfw_indices": (2, 3),
        "labels": ("neutral", "low", "medium", "high"),
        "score_desc": "medium + high 概率之和（4 档有序；low 是擦边不计入，拦色情只取后两档）",
    },
    "nsfwjs-mobilenet-v2": {
        "label": "NSFW.js MobileNetV2",
        "model_version": "nsfwjs/mobilenet_v2",
        "weight": "nsfwjs-mobilenet-v2.onnx",
        "sha256": "c8c96945be2a009993e8b5bb470379931f51f859a17830ec5cf92cd456954501",
        "input_size": 224,
        "interp": "bilinear",
        "mean": (0.0, 0.0, 0.0),
        "std": (1.0, 1.0, 1.0),
        "layout": "NHWC",
        "softmax": False,  # 图内已含 Softmax，再 softmax 会二次压缩分布
        "score_index": None,
        "nsfw_indices": (1, 3, 4),
        "labels": ("Drawing", "Hentai", "Neutral", "Porn", "Sexy"),
        "score_desc": "Hentai + Porn + Sexy 三类概率之和（5 类）",
    },
}

_sessions: dict[str, object] = {}
_errors: dict[str, tuple[BaseException, int | None]] = {}


def provider_spec(provider: str | None = None) -> dict:
    """取方案定义；未知 provider 回退默认方案。

    回退而不是抛错：settings.json 是人工可编辑文件，写坏一个方案名不该让整条
    鉴黄链路直接不可用——按默认方案继续工作，UI 上由 provider 字段比对暴露差异。
    """
    return PROVIDERS.get(provider or config.nsfw_settings()["provider"]) or PROVIDERS[DEFAULT_PROVIDER]


def current_provider() -> str:
    pid = config.nsfw_settings()["provider"]
    return pid if pid in PROVIDERS else DEFAULT_PROVIDER


def weight_path(provider: str | None = None) -> Path:
    """方案权重路径。默认方案沿用 config.NSFW_MODEL_PATH（含 AI_MEDIA_NSFW_MODEL 覆盖）。

    AI_MEDIA_NSFW_MODEL 只对默认方案生效：它是给宿主指定「另一个 falconsai 权重
    副本」用的。若让它对其它方案也生效，切到 EVA02 时会拿 falconsai 的权重配
    EVA02 的 448×448 预处理——shape 不匹配时直接报错，若尺寸恰好兼容则会静默
    产出无意义的分数，比报错更难发现。
    """
    pid = provider or current_provider()
    if pid == DEFAULT_PROVIDER:
        return config.NSFW_MODEL_PATH
    return config.DATA_DIR / PROVIDERS[pid]["weight"]


def status(provider: str | None = None) -> str:
    pid = provider or current_provider()
    if pid in _sessions:
        return "ready"
    if pid in _errors:
        return "failed"
    return "unloaded"


def _load(provider: str | None = None):
    pid = provider or current_provider()
    spec = PROVIDERS[pid]
    session = _sessions.get(pid)
    if session is not None:
        return session
    path = weight_path(pid)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    cached = _errors.get(pid)
    if cached is not None:
        err, err_mtime = cached
        # 权重文件没换过就沿用上次失败结论，不再反复尝试加载（扫描期每张图都会问）。
        if mtime == err_mtime:
            raise RuntimeError("review_unavailable") from err
        _errors.pop(pid, None)
    try:
        import onnxruntime as ort
        if not path.is_file():
            raise FileNotFoundError(f"NSFW 权重未安装：{path}")
        _sessions[pid] = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        return _sessions[pid]
    except Exception as e:  # noqa: BLE001 - 加载失败要降级为「本方案不可用」
        _errors[pid] = (e, mtime)
        raise RuntimeError("review_unavailable") from e


def available(provider: str | None = None) -> bool:
    """权重文件是否就位。只查文件，不加载模型——扫描期每张图都问一次。

    保持布尔返回值：调用方（扫描循环）只关心「能不能跑」，方案名/权重名这类
    展示信息由路由层用 PROVIDERS + weight_path 组装，不塞进这个热路径。
    """
    return weight_path(provider or current_provider()).is_file()


def score_image(data, provider: str | None = None):
    """扫描期初筛：返回 {score,label,status} 或 None。

    模型没装、开关关掉、推理失败都返回 None（=「这轮没结论」），调用方跳过写库，
    扫描本身照常完成。初筛是全量扫描的附带动作，不该因为它失败就让整轮扫描 partial。
    """
    pid = provider or current_provider()
    if not config.nsfw_settings()["auto_scan"] or not available(pid):
        return None
    result = review_bytes(data, pid)
    return result if result.get("status") == "ok" else None


def _softmax_probs(logits):
    """logits [n] -> softmax 概率，输出与输入同形状。"""
    import numpy as np
    z = np.asarray(logits, dtype=np.float32)
    z = z - np.max(z)
    e = np.exp(z)
    return e / np.sum(e)


def _nsfw_score(probs, spec: dict) -> float:
    if spec["nsfw_indices"] is None:
        index = spec["score_index"]
        return float(probs[index]) if len(probs) > index else float(probs[0])
    total = 0.0
    for index in spec["nsfw_indices"]:
        if index < len(probs):
            total += float(probs[index])
    return total


def _prepare(im, spec: dict):
    """按 spec 做预处理，返回可直接喂给 session.run 的张量。

    三个方案的差异全部由字段驱动：尺寸、插值、归一化、布局。
    """
    import numpy as np
    from PIL import Image

    size = spec["input_size"]
    resample = Image.BICUBIC if spec.get("interp") == "bicubic" else Image.BILINEAR
    im = im.convert("RGB").resize((size, size), resample)
    x = np.asarray(im, dtype=np.float32) / 255.0
    mean = np.asarray(spec["mean"], dtype=np.float32)
    std = np.asarray(spec["std"], dtype=np.float32)
    x = (x - mean) / std
    if spec.get("layout") == "NHWC":
        return x[None, ...]
    return np.transpose(x, (2, 0, 1))[None, ...]


def review_bytes(data, provider: str | None = None):
    pid = provider or current_provider()
    spec = PROVIDERS[pid]
    try:
        import numpy as np
        from PIL import Image
        from io import BytesIO

        model = _load(pid)
        input_meta = model.get_inputs()[0]
        with Image.open(BytesIO(data)) as im:
            x = _prepare(im, spec)
        result = model.run(None, {input_meta.name: x})[0].reshape(-1)
        # 图内已含 Softmax 的方案不能再算一次：softmax 会二次压缩分布，
        # 「低危 0.917」这类中间刻度会被推向两端，阈值语义随之失真。
        probs = _softmax_probs(result) if spec["softmax"] else np.asarray(result, dtype=np.float32)
        score = _nsfw_score(probs, spec)
        threshold = config.nsfw_settings()["threshold"]
        return {
            "score": score,
            "label": "nsfw" if score >= threshold else "safe",
            "status": "ok",
            "provider": pid,
        }
    except Exception as e:  # noqa: BLE001 - 单图失败只影响该图，不中断扫描
        return {"score": None, "label": None, "status": "failed", "error": str(e)[:200], "provider": pid}
