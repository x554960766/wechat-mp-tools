"""可选 NSFW 审查；按需加载，不影响普通检索。

口径与来源实现 nas-image-search/review.py 一致，并与 Falconsai/nsfw_image_detection
官方配置（ViTImageProcessor + ViTForImageClassification）逐项核对过：
- 预处理 resize 224（bilinear）-> /255 -> (x-0.5)/0.5 -> NCHW
- 模型输出 2 维 logits [normal, nsfw]（id2label 0=normal 1=nsfw），图内无 Softmax，
  必须调用方 softmax 后取 probs[1] 作 nsfw 分数
- 阈值 0.5，只有 score >= 0.5 判 nsfw（不能把 logit 当概率比阈值）

权重路径来自 config.NSFW_MODEL_PATH（默认 <DATA_DIR>/nsfw.onnx，由宿主注入的
AI_MEDIA_DATA_DIR 决定），可用 AI_MEDIA_NSFW_MODEL 覆盖；SHA-256 见 config 常量。
"""
from __future__ import annotations
from . import config
_model=None; _error=None
THRESHOLD=0.5
IMAGE_SIZE=224

def status(): return "ready" if _model is not None else ("failed" if _error else "unloaded")

def _load():
    global _model,_error
    if _model is not None:return _model
    if _error: raise RuntimeError("review_unavailable") from _error
    try:
        import onnxruntime as ort
        path=config.NSFW_MODEL_PATH
        if not path.is_file(): raise FileNotFoundError(f"NSFW 权重未安装：{path}")
        session=ort.InferenceSession(str(path),providers=["CPUExecutionProvider"])
        _model=session; return _model
    except Exception as e: _error=e; raise RuntimeError("review_unavailable") from e

def available():
    """权重文件是否就位。只查文件，不加载模型——扫描期每张图都问一次。"""
    return config.NSFW_MODEL_PATH.is_file()

def score_image(data):
    """扫描期初筛：返回 {score,label,status} 或 None。

    模型没装、开关关掉、推理失败都返回 None（=「这轮没结论」），调用方跳过写库，
    扫描本身照常完成。初筛是全量扫描的附带动作，不该因为它失败就让整轮扫描 partial。
    """
    if not config.NSFW_SCAN or not available():
        return None
    result = review_bytes(data)
    return result if result.get("status") == "ok" else None

def _softmax_probs(logits):
    """logits [n] -> softmax 概率，输出与输入同形状。"""
    import numpy as np
    z=np.asarray(logits,dtype=np.float32)
    z=z-np.max(z)
    e=np.exp(z)
    return e/np.sum(e)

def review_bytes(data):
    try:
        import numpy as np
        from PIL import Image
        from io import BytesIO
        model=_load(); input_meta=model.get_inputs()[0]
        with Image.open(BytesIO(data)) as im:
            im=im.convert("RGB").resize((IMAGE_SIZE,IMAGE_SIZE),Image.BILINEAR)
            x=np.asarray(im,dtype=np.float32)/255.0
        x=(x-0.5)/0.5
        x=np.transpose(x,(2,0,1))[None,...]
        result=model.run(None,{input_meta.name:x})[0].reshape(-1)
        probs=_softmax_probs(result)
        score=float(probs[1]) if len(probs)>=2 else float(probs[0])
        return {"score":score,"label":"nsfw" if score>=THRESHOLD else "safe","status":"ok"}
    except Exception as e:
        return {"score":None,"label":None,"status":"failed","error":str(e)[:200]}
