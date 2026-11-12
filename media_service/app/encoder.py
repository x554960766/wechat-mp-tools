"""Chinese-CLIP 的惰性单例编码入口。"""
from __future__ import annotations
import hashlib, threading
import numpy as np
from PIL import Image
from io import BytesIO

from . import config

_lock=threading.RLock(); _requests=threading.BoundedSemaphore(17); _model=None; _processor=None; _device=None; _error=None
def fingerprint():
    value=f"{config.MODEL_ID}@{config.MODEL_REVISION}|processor=ChineseCLIPProcessor:slow:max_length512|projection=pooler-or-cls|image=RGB|dim=512|float32|l2"
    return hashlib.sha256(value.encode()).hexdigest()

def _load():
    global _model,_processor,_device,_error
    with _lock:
        if _model is not None: return _model,_processor,_device
        if _error: raise RuntimeError("encoder_failed") from _error
        try:
            import torch
            from transformers import ChineseCLIPModel, ChineseCLIPProcessor
            _device="mps" if torch.backends.mps.is_available() else "cpu"
            revision=config.MODEL_REVISION or None
            _processor=ChineseCLIPProcessor.from_pretrained(config.MODEL_ID,revision=revision,use_fast=False)
            _model=ChineseCLIPModel.from_pretrained(config.MODEL_ID,revision=revision).to(_device).eval()
            return _model,_processor,_device
        except Exception as e:
            _error=e; raise RuntimeError("encoder_load_failed") from e

def _vector(output):
    x=getattr(output,"pooler_output",None)
    if x is None: x=output
    if hasattr(x,"ndim") and x.ndim==3: x=x[:,0,:]
    if x.ndim != 2 or x.shape[-1] != 512: raise ValueError(f"模型输出维度错误：{tuple(x.shape)}")
    x=x.float(); norms=x.norm(dim=-1,keepdim=True)
    if not bool((norms>1e-8).all()) or not bool(x.isfinite().all()): raise ValueError("模型输出不是有限非零向量")
    return (x/norms).detach().cpu().numpy().astype(np.float32)

def encode_image(data: bytes):
    if not _requests.acquire(blocking=False): raise RuntimeError("model_queue_full")
    try: return _encode_image(data)
    finally: _requests.release()

def _encode_image(data: bytes):
    from .media import register_heif
    register_heif()
    with Image.open(BytesIO(data)) as im:
        from .media import check_pixels
        check_pixels(im)
        im.load(); im=im.convert("RGB")
    model,processor,device=_load()
    with _lock:
        inputs=processor(images=[im],return_tensors="pt")
        inputs={k:v.to(device) for k,v in inputs.items()}
        import torch
        with torch.inference_mode():
            out=model.vision_model(pixel_values=inputs["pixel_values"])
            pooled=getattr(out,"pooler_output",None)
            if pooled is None: pooled=out.last_hidden_state[:,0,:]
            out=model.visual_projection(pooled)
        return _vector(out)[0]

def encode_texts(texts):
    if not _requests.acquire(blocking=False): raise RuntimeError("model_queue_full")
    try: return _encode_texts(texts)
    finally: _requests.release()

def _encode_texts(texts):
    model,processor,device=_load()
    with _lock:
        inputs=processor(text=texts,return_tensors="pt",padding=True,truncation=True,max_length=512)
        inputs={k:v.to(device) for k,v in inputs.items()}
        import torch
        with torch.inference_mode():
            out=model.text_model(**inputs)
            pooled=getattr(out,"pooler_output",None)
            if pooled is None: pooled=out.last_hidden_state[:,0,:]
            out=model.text_projection(pooled)
        return _vector(out)

def status():
    if _model is not None: return {"state":"ready","error":None,"fingerprint":fingerprint()}
    return {"state":"failed" if _error else "unloaded","error":str(_error) if _error else None,"fingerprint":fingerprint()}
