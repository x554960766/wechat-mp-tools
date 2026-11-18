"""共享图片查看器（frontend/js/utils/media_viewer.js）的不变量守护。

查看器已从 media_search 抽取为多页复用的共享组件，media_search 与全局文件检索都依赖它。
与 ``test_panorama_ui.py`` 对媒体服务自带灯箱的守护同思路：这三条不变量一旦只写在注释里就会被越过，
所以钉在测试上，防止抽取或后续改动悄悄破坏全景口径与资源释放。

1. 请求代次必须在首个 await 之前建立，等待期间退出/切图才作废得了；
2. 关闭入口统一释放 GPU 资源（纹理/几何/材质/渲染器/canvas）；
3. 上下立体只取上半幅单眼，且用纹理取幅而不是两个半高视口。
"""
from pathlib import Path

import pytest

VIEWER = Path(__file__).resolve().parents[1] / "frontend" / "js" / "utils" / "media_viewer.js"


@pytest.fixture(scope="module")
def source():
    return VIEWER.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_panorama_token_created_before_first_await(source):
    body = _between(source, "async panorama()", "if (this._viewer !== viewer || token !== viewer.seq) return;")
    assert body.index("const token = ++viewer.seq;") < body.index("await this.loadThree()")
    # await 之后必须核对代次，否则旧请求仍会把场景装进已经退出的查看器
    assert "if (this._viewer !== viewer || token !== viewer.seq) return;" in source


def test_close_releases_gpu_resources(source):
    dispose = _between(source, "disposePano(pano) {", "status(msg)")
    for call in ("pano.geometry.dispose()", "pano.texture.dispose()",
                 "pano.material.dispose()", "pano.renderer.dispose()",
                 "pano.renderer.domElement"):
        assert call in dispose, f"disposePano 必须释放 {call}"
    # 关闭入口必须走同一个释放入口，而不是各写一套清理
    close = _between(source, "close() {", "window.MediaViewer")
    assert "this.disposePano(pano);" in close


def test_stereo_uses_upper_half_single_eye(source):
    mode = _between(source, "applyPanoMode() {", "exitPano()")
    assert "pano.texture.repeat.y = 0.5;" in mode
    assert "pano.texture.offset.y = 0.5;" in mode
    assert "pano.texture.repeat.y = 1;" in mode
    assert "pano.texture.offset.y = 0;" in mode
    # 只建一个球面网格、一个视口：用两个半高视口会让画面比例失真
    build = _between(source, "buildPano(THREE, image, viewer) {", "applyPanoMode()")
    assert "new THREE.SphereGeometry(500, 64, 40)" in build
    assert build.count("new THREE.MeshBasicMaterial") == 1


def test_viewer_is_exported_and_prefixed(source):
    assert "window.MediaViewer = MediaViewer;" in source
    # DOM 前缀是多页复用的唯一隔离手段：id 必须经前缀生成，不许硬编码 ms- 前缀
    assert "return `${this.prefix}-${name}`;" in source
    assert "this.prefix = prefix;" in source
