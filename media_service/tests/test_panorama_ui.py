"""全景预览的约束守护：这些不变量只写在注释里会被越过，所以钉在测试上。

三条对应 Spec《图片鉴黄与VR全景预览》里的审查缺陷：
1. 请求代次必须在首个 await 之前建立，等待期间退出/切图才作废得了；
2. dialog 的原生关闭（Esc / cancel / close）必须走同一个资源释放入口；
3. 上下立体只取上半幅单眼，且用纹理取幅而不是两个半高视口。
"""
from pathlib import Path

import pytest

INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"


@pytest.fixture(scope="module")
def source():
    return INDEX.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_token_created_before_first_await(source):
    body = _between(source, "async function enterPanorama", "img.src=src;")
    assert body.index("const token=++panoSeq;") < body.index("await loadThree()")
    # await 之后必须核对代次，否则旧请求仍会把场景装进已经退出的灯箱
    assert "if(token!==panoSeq)return;" in body


def test_dialog_close_releases_gpu_resources(source):
    assert "$('lightbox').addEventListener('close',disposePano);" in source
    dispose = _between(source, "function disposePano(){", "$('pano-stage')")
    for call in ("pano.texture?.dispose()", "pano.mesh?.geometry?.dispose()",
                 "pano.mesh?.material?.dispose()", "pano.renderer?.dispose()"):
        assert call in dispose, f"disposePano 必须释放 {call}"


def test_stereo_uses_upper_half_single_eye(source):
    apply_mode = _between(source, "function applyPanoMode(){", "function panoResize()")
    assert "t.repeat.y=0.5;t.offset.y=0.5" in apply_mode
    assert "t.repeat.y=1;t.offset.y=0" in apply_mode
    # 只用整窗口一个视口：双视口共用整画布宽高比会让画面比例失真
    render = _between(source, "function panoRender(){", "async function enterPanorama")
    assert "r.setViewport(0,0,w,h);r.setScissor(0,0,w,h);" in render
    assert render.count("setViewport") == 1
