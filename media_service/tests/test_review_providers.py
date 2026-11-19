"""三个鉴黄方案的预处理与打分语义测试。

方案之间的差异全部由 PROVIDERS 的 spec 字段驱动（尺寸/插值/归一化/布局/是否
自带 softmax），review_bytes 不写 `if provider == ...` 分支。这里锁住三件事：

1. 每个方案的预处理真的按自己的 spec 执行（EVA02 是 448 + bicubic + CLIP 归一化，
   MobileNet 是 224 + 仅 /255 + NHWC）——写错任一项都会静默给出垃圾分；
2. 图内已含 Softmax 的方案不能被二次 softmax（概率分布会被再次压缩，阈值语义失真）；
3. 新增方案必须补齐全部预处理字段，否则扫描期才 KeyError。
"""
from __future__ import annotations
import io
import numpy as np
from PIL import Image
from app import review


class FakeSession:
    """回放固定输出（形状由方案决定），并捕获喂进去的张量。"""

    def __init__(self, output, captured=None):
        self._output = np.asarray(output, np.float32)
        self.captured = captured

    def get_inputs(self):
        return [type("I", (), {"name": "input"})()]

    def run(self, _, feed):
        x = feed["input"]
        if self.captured is not None:
            self.captured.append(x.copy())
        return [self._output]


def _png(rgb=(128, 200, 7)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), rgb).save(buf, format="PNG")
    return buf.getvalue()


def _run(provider: str, output, captured=None) -> dict:
    review._sessions[provider] = FakeSession(output, captured)
    try:
        return review.review_bytes(_png(), provider)
    finally:
        review._sessions.pop(provider, None)
        review._errors.pop(provider, None)


def _softmax(z):
    z = np.asarray(z, np.float64)
    e = np.exp(z - z.max())
    return e / e.sum()


def test_eva02_prepare_is_448_bicubic_clip_nchw():
    """EVA02 必须 448×448 + CLIP mean/std + NCHW，不能沿用默认方案的 ±1 归一化。"""
    cap: list[np.ndarray] = []
    spec = review.PROVIDERS["freepik-eva02"]
    _run("freepik-eva02", [0.0, 0.0, 0.0, 0.0], cap)
    x = cap[0]
    assert x.shape == (1, 3, 448, 448)
    for c, v in enumerate((128, 200, 7)):
        expected = (v / 255.0 - spec["mean"][c]) / spec["std"][c]
        assert abs(float(x[0, c, 224, 224]) - expected) < 1e-5


def test_mobilenet_prepare_is_nhwc_255_only():
    """NSFW.js 只需 /255 且是 NHWC；(x-0.5)/0.5 和 NCHW 都会喂出垃圾分。"""
    cap: list[np.ndarray] = []
    _run("nsfwjs-mobilenet-v2", [0.2, 0.2, 0.2, 0.2, 0.2], cap)
    x = cap[0]
    assert x.shape == (1, 224, 224, 3)
    assert abs(float(x[0, 112, 112, 0]) - 128 / 255.0) < 1e-6
    assert abs(float(x[0, 112, 112, 2]) - 7 / 255.0) < 1e-6


def test_eva02_score_excludes_low_tier():
    """4 档有序输出：score = medium+high。low（擦边）不计入——实测白裙街拍会拿到
    low≈0.99，若把 low 算进来，阈值 0.35 会把整篇写真壁纸判成违规。"""
    logits = [1.0, 4.0, -1.0, -2.0]          # low 占绝对多数
    r = _run("freepik-eva02", logits)
    probs = _softmax(logits)
    assert r["status"] == "ok", r
    assert abs(r["score"] - float(probs[2] + probs[3])) < 1e-6
    assert r["label"] == "safe", r
    # 与旧定义（1−neutral，把 low 也算进去）必须有可观测差异，否则「擦边放过」是空话
    assert float(1.0 - probs[0]) - float(r["score"]) > 0.5


def test_eva02_medium_tier_still_trips_threshold():
    """跳过 low 不能变成「什么都不拦」：medium 档必须能判违规。"""
    r = _run("freepik-eva02", [-2.0, -1.0, 4.0, 0.5])
    assert r["status"] == "ok", r
    assert r["label"] == "nsfw", r
    assert r["score"] > 0.9


def test_mobilenet_output_is_not_double_softmaxed():
    """NSFW.js 图内已含 Softmax：分数必须是原样求和，再 softmax 会把 0.4 压成 0.29。"""
    probs = [0.05, 0.10, 0.55, 0.25, 0.05]  # 行和 1.0
    r = _run("nsfwjs-mobilenet-v2", probs)
    assert r["status"] == "ok", r
    assert abs(r["score"] - 0.40) < 1e-6            # 原样：0.10+0.25+0.05
    assert abs(r["score"] - float(_softmax(probs)[[1, 3, 4]].sum())) > 0.05  # 二次 softmax 必然偏离


def test_default_provider_still_softmaxes_logits():
    """默认方案图内无 Softmax，仍必须由调用方 softmax（回归保护）。"""
    logits = [-5.0, -0.5]
    r = _run("falconsai-vit-b16", logits)
    assert abs(r["score"] - float(_softmax(logits)[1])) < 1e-6
    assert r["label"] == "nsfw"


def test_every_provider_declares_full_preprocess_spec():
    """新增方案漏字段会在扫描期才 KeyError——这里提前拦截。"""
    required = {"input_size", "interp", "mean", "std", "layout", "softmax",
                "score_index", "nsfw_indices", "labels", "weight", "sha256", "model_version"}
    for pid, spec in review.PROVIDERS.items():
        missing = required - set(spec)
        assert not missing, f"{pid} 缺少字段: {sorted(missing)}"
        assert spec["interp"] in ("bilinear", "bicubic"), pid
        assert spec["layout"] in ("NCHW", "NHWC"), pid
        assert len(spec["mean"]) == len(spec["std"]) == 3, pid
        # 有 nsfw_indices 就必须都落在类别数内
        if spec["nsfw_indices"] is not None:
            assert max(spec["nsfw_indices"]) < len(spec["labels"]), pid


def test_weight_override_only_applies_to_default_provider(monkeypatch):
    """AI_MEDIA_NSFW_MODEL 只覆盖默认方案。

    若让它对其它方案生效，切到 EVA02 会拿 falconsai 的权重配 448×448 + CLIP 预处理：
    尺寸不匹配时直接报错，尺寸恰好兼容时则静默给出无意义分数——比报错更难发现。
    """
    from pathlib import Path
    custom = Path("/tmp/custom-falconsai.onnx")
    monkeypatch.setattr(review.config, "NSFW_MODEL_PATH", custom)

    assert review.weight_path("falconsai-vit-b16") == custom
    for pid in ("freepik-eva02", "nsfwjs-mobilenet-v2"):
        assert review.weight_path(pid) == review.config.DATA_DIR / review.PROVIDERS[pid]["weight"]
