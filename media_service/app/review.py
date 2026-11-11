"""可选 NSFW 审查；按需加载，不影响普通检索。

口径与来源实现 nas-image-search/review.py 一致（Spec §237 复用该模块）：
- 预处理 resize 224 -> /255 -> (x-0.5)/0.5 -> NCHW
- 模型输出 2 维 logits [normal, nsfw]，softmax 后取 probs[1] 作 nsfw 分数
- 阈值 0.5，只有 score >= 0.5 判 nsfw（不能把 logit 当概率比阈值）
"""
from __future__ import annotations
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
        from pathlib import Path
        path=Path(__file__).resolve().parent.parent/"data"/"nsfw.onnx"
        if not path.is_file(): raise FileNotFoundError("data/nsfw.onnx 未安装")
        session=ort.InferenceSession(str(path),providers=["CPUExecutionProvider"])
        _model=session; return _model
    except Exception as e: _error=e; raise RuntimeError("review_unavailable") from e

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
            im=im.convert("RGB").resize((IMAGE_SIZE,IMAGE_SIZE))
            x=np.asarray(im,dtype=np.float32)/255.0
        x=(x-0.5)/0.5
        x=np.transpose(x,(2,0,1))[None,...]
        result=model.run(None,{input_meta.name:x})[0].reshape(-1)
        probs=_softmax_probs(result)
        score=float(probs[1]) if len(probs)>=2 else float(probs[0])
        return {"score":score,"label":"nsfw" if score>=THRESHOLD else "safe","status":"ok"}
    except Exception as e:
        return {"score":None,"label":None,"status":"failed","error":str(e)[:200]}
