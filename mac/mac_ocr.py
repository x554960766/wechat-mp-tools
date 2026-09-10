"""macOS 原生 Vision 框架 OCR 封装。"""
from __future__ import annotations

from dataclasses import dataclass
import cv2
import Vision
from Foundation import NSData


@dataclass
class TextBox:
    """OCR 识别到的文本块对象。坐标系为传入图像的像素坐标。"""
    text: str
    score: float
    left: int
    top: int
    right: int
    bottom: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def width(self) -> int:
        return self.right - self.left

    def __repr__(self) -> str:
        return f"TextBox({self.text!r}, conf={self.score:.2f}, rect=({self.left},{self.top},{self.right},{self.bottom}))"


def ocr(img_bgr, upscale: float = 1.0, min_score: float = 0.3) -> list[TextBox]:
    """
    对 BGR 图像做 OCR。坐标返回的是传入图像的像素坐标。
    使用 macOS Vision 框架，高精度识别中英文。
    """
    if upscale != 1.0:
        img_bgr = cv2.resize(img_bgr, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)

    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        return []
    ns_data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLanguages_(["zh-Hans", "en-US"])
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ns_data, None)
    handler.performRequests_error_([request], None)

    h, w = img_bgr.shape[:2]
    boxes: list[TextBox] = []
    for obs in (request.results() or []):
        cands = obs.topCandidates_(1)
        if not cands:
            continue
        text = cands[0].string().strip()
        # pyobjc 兼容：部分版本 confidence 是 selector，需调用
        _cf = cands[0].confidence
        conf = float(_cf() if callable(_cf) else _cf)
        if not text or conf < min_score:
            continue
        bb = obs.boundingBox()   # 归一化坐标，原点在左下角
        left = int(bb.origin.x * w / upscale)
        right = int((bb.origin.x + bb.size.width) * w / upscale)
        top = int((1 - bb.origin.y - bb.size.height) * h / upscale)  # y 轴翻转
        bottom = int((1 - bb.origin.y) * h / upscale)
        boxes.append(TextBox(text, conf, left, top, right, bottom))

    # 按自上而下、从左到右排序
    boxes.sort(key=lambda b: (b.top // 12, b.left))
    return boxes


def find_text(boxes: list[TextBox], keyword: str, exact: bool = False) -> list[TextBox]:
    """在识别结果中查找包含或完全匹配 keyword 的文本块。"""
    if exact:
        return [b for b in boxes if b.text == keyword]
    return [b for b in boxes if keyword in b.text]


def find_text_fuzzy(boxes: list[TextBox], keyword: str, threshold: float = 0.6) -> list[TextBox]:
    """使用 Jaccard 相似度进行模糊匹配。"""
    kw = set(keyword)
    out = []
    for b in boxes:
        t = set(b.text)
        if not t:
            continue
        sim = len(kw & t) / len(kw | t)
        if sim >= threshold or keyword in b.text:
            out.append(b)
    return out


def dump_debug(img, boxes: list[TextBox], path: str) -> None:
    """在图像上绘制识别出的文本矩形框并保存，便于排查调试。"""
    vis = img.copy()
    for b in boxes:
        cv2.rectangle(vis, (b.left, b.top), (b.right, b.bottom), (0, 200, 0), 1)
        cv2.putText(vis, b.text[:10], (b.left, max(b.top - 2, 10)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    cv2.imwrite(path, vis)
