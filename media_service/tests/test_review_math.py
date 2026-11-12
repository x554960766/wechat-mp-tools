"""NSFW 审查的数学口径测试：logits 必须 softmax 成概率后才比 0.5。

来源实现 nas-image-search/review.py 的口径：
  - 预处理 resize 224 -> /255 -> (x-0.5)/0.5 -> NCHW
  - 输出 2 维 logits [normal, nsfw]，softmax 后取 probs[1] 作 nsfw 概率
  - score >= 0.5 判 nsfw

如果把 logit 直接当概率比阈值，logit 为负但 nsfw 概率很高（即 logits=[-5,-0.5]）
会被判成 safe —— 这正是本测试要拦的行为。
"""
from __future__ import annotations
import io
import numpy as np
from PIL import Image
from app import review


class FakeSession:
    """捕获输入，回放固定 logits（不用真模型）。"""
    def __init__(self, logits, captured=None):
        self._logits = np.asarray(logits, np.float32).reshape(1, 2)
        self.captured = captured
    def get_inputs(self):
        return [type("I", (), {"name": "pixel_values"})()]
    def run(self, _, feed):
        x = feed["pixel_values"]
        if self.captured is not None:
            self.captured.append(x.copy())
        return [self._logits]


def _png() -> bytes:
    """一张纯色小 PNG；单色图像任意 resample 都保持原值，便于精确断言预处理。"""
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (128, 200, 7)).save(buf, format="PNG")
    return buf.getvalue()


def _prep_expected(v: float) -> float:
    return float(np.float32(v / 255.0 - 0.5) / 0.5)


def _patch_logits(logits, captured=None) -> dict:
    review._error = None
    review._model = FakeSession(logits, captured)
    try:
        return review.review_bytes(_png())
    finally:
        review._model = None
        review._error = None


def test_softmax_positive_nsfw_logit_score():
    """logits=[5.0, 0.5]：logit 为正但 softmax 后 nsfw 概率只有约 0.011，应判 safe。"""
    r = _patch_logits([5.0, 0.5])
    assert r["status"] == "ok", r
    assert r["score"] is not None and 0.0 <= r["score"] <= 1.0
    assert r["score"] < 0.5
    assert r["label"] == "safe"


def test_softmax_negative_logit_but_high_nsfw_prob():
    """logits=[-5, -0.5]：logit 全为负，但 softmax 后 nsfw 概率约 0.989，必须判 nsfw。"""
    r = _patch_logits([-5.0, -0.5])
    assert r["status"] == "ok", r
    assert r["score"] > 0.5
    assert r["label"] == "nsfw"


def test_preprocess_normalizes_and_nchw():
    """预处理必须 (v/255-0.5)/0.5 归一化 + NCHW 布局，而不是除完 255 直接喂。"""
    cap: list[np.ndarray] = []
    review._error = None
    review._model = FakeSession([0.0, 0.0], captured=cap)
    try:
        review.review_bytes(_png())
    finally:
        review._model = None
        review._error = None
    x = cap[0]
    assert x.shape == (1, 3, 224, 224)
    assert abs(float(x[0, 0, 0, 0]) - _prep_expected(128)) < 1e-6
    assert abs(float(x[0, 1, 0, 0]) - _prep_expected(200)) < 1e-6
    assert abs(float(x[0, 2, 0, 0]) - _prep_expected(7)) < 1e-6
