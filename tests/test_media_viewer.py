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
STYLES = Path(__file__).resolve().parents[1] / "frontend" / "css" / "style.css"


@pytest.fixture(scope="module")
def source():
    return VIEWER.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles():
    return STYLES.read_text(encoding="utf-8")


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


def test_stage_height_is_fixed_so_zoom_cannot_reflow(source, styles):
    """舞台高度必须自己定死。

    舞台一旦跟着图片自适应，缩放就会一边缩一边改布局、旋转后的适配尺寸也没有稳定基准；
    所以缩放/旋转/平移只许落在 transform 上，布局尺寸只在 layout() 里算一次。
    """
    stage = _between(styles, ".ms-viewer-stage {", ".ms-viewer-stage img {")
    assert "flex: 0 0 auto;" in stage
    assert "height: clamp(" in stage
    apply_view = _between(source, "applyView() {", "_syncViewChrome() {")
    assert "img.style.transform =" in apply_view
    assert "img.style.width" not in apply_view
    assert "img.style.height" not in apply_view
    layout = _between(source, "layout() {", "applyView() {")
    assert "view.baseW = bh * ratio;" in layout


def test_rotate_refits_by_bounding_box(source):
    layout = _between(source, "layout() {", "applyView() {")
    assert "Math.abs(Math.cos(rad))" in layout
    assert "Math.abs(Math.sin(rad))" in layout
    # 适配尺寸按旋转后的外接框算，否则 90° 后两侧会被裁掉
    rotate = _between(source, "rotateBy(delta) {", "toggleLens() {")
    assert "this.layout();" in rotate
    assert "view.tx = 0;" in rotate and "view.ty = 0;" in rotate


def test_zoom_anchors_on_cursor_within_limits(source):
    zoom = _between(source, "zoomTo(nextScale, clientX, clientY) {", "zoomBy(factor) {")
    # 1–8 倍：低于 1 没有意义，高于 8 只剩马赛克
    assert "const next = Math.max(1, Math.min(8, nextScale));" in zoom
    # 光标下的图内点保持不动（先反解到基准坐标，再按新倍率放回去）
    assert "view.tx = qx - next * (px * cos - py * sin);" in zoom
    assert "view.ty = qy - next * (px * sin + py * cos);" in zoom


def test_lens_geometry_matches_styles(styles, source):
    """镜片 180px 与 margin -90px 必须一起改，否则放大的取样点会偏出镜心。"""
    lens = _between(styles, ".ms-viewer-lens {", ".ms-viewer-lens[hidden]")
    assert "width: 180px;" in lens
    assert "height: 180px;" in lens
    assert "margin: -90px 0 0 -90px;" in lens
    body = _between(source, "updateLensAt(clientX, clientY) {", "async panorama() {")
    assert "const zoom = Math.min(8, Math.max(1, view.scale * view.lensZoom));" in body
    # 镜内倍率读数要跟镜片一起出现，否则滚轮调倍率时没有反馈
    assert "badge.textContent = `×${zoom.toFixed(1)}`;" in body


def test_lens_zoom_is_wheel_adjustable_and_locks_the_image(source):
    """放大镜开启时滚轮只调镜内倍率：画面整体锁定，不许缩放、不许拖动。"""
    assert "lens: false, lensZoom: 2," in source
    wheel = _between(source, "stage.addEventListener('wheel'", "stage.addEventListener('dblclick'")
    branch = wheel[wheel.index("if (view.lens) {"):wheel.index("this.zoomTo(")]
    assert "this.setLensZoom(view.lensZoom * Math.exp(-event.deltaY * 0.0015), event.clientX, event.clientY);" in branch
    assert "return;" in branch  # 命中镜内倍率后必须早退，不能落回整图缩放
    down = _between(source, "stage.addEventListener('pointerdown'", "stage.addEventListener('pointermove'")
    assert "if (view.lens) return; // 放大镜开启时画面整体锁定，指针只移动镜片" in down
    lens_zoom = _between(source, "setLensZoom(next, clientX, clientY) {", "/** 标准与 webkit")
    assert "Math.max(1.5, Math.min(8, next))" in lens_zoom


def test_fullscreen_uses_viewer_box_and_guards_escape(source, styles):
    """全屏挂在查看器根节点上；Esc 先退全屏再关窗；close 要摘监听并主动退出全屏。"""
    markup = _between(source, "box.innerHTML = `", "document.body.appendChild(box);")
    assert 'data-act="fullscreen"' in markup
    assert "viewer.box.requestFullscreen || viewer.box.webkitRequestFullscreen" in source
    keys = _between(source, "this._keys = (event) => {", "document.addEventListener('keydown', this._keys);")
    assert "if (this._fullscreenEl()) return;" in keys  # 全屏时 Esc 先交还浏览器
    assert "if (event.key === 'f' || event.key === 'F') this.toggleFullscreen();" in keys
    fs = _between(source, "this._onFsChange = () => {", "this._keys = (event) => {")
    assert "classList.toggle('is-fullscreen', on);" in fs
    assert "this.layout();" in fs  # 舞台尺寸变了必须重算适配尺寸
    close = _between(source, "close() {", "window.MediaViewer")
    assert "document.removeEventListener('fullscreenchange', this._onFsChange);" in close
    assert "document.removeEventListener('webkitfullscreenchange', this._onFsChange);" in close
    assert "if (this._fullscreenEl() === viewer.box) this.exitFullscreen();" in close
    # 全屏舞台靠 flex 吃剩余高度，标签行有无都不会溢出
    fs_stage = _between(styles, ".ms-viewer.is-fullscreen .ms-viewer-stage {", ".ms-viewer-pano {")
    assert "flex: 1 1 auto;" in fs_stage
    assert "min-height: 0;" in fs_stage


def test_panorama_hides_planar_zoom_chrome(source, styles):
    build = _between(source, "buildPano(THREE, image, viewer) {", "applyPanoMode() {")
    assert "zoomBar.hidden = true;" in build
    assert "this.hideLens();" in build
    exit_pano = _between(source, "exitPano() {", "disposePano(pano) {")
    assert "zoomBar.hidden = false;" in exit_pano
    # 作者样式的 display 会压过 UA 的 [hidden]：全景控件必须显式补一条隐藏规则
    assert ".ms-viewer-pano-controls[hidden] { display: none; }" in styles


def test_close_removes_zoom_listeners(source):
    close = _between(source, "close() {", "window.MediaViewer")
    assert "window.removeEventListener('resize', this._onResize);" in close
    assert "document.removeEventListener('keydown', this._keys);" in close


def test_viewer_is_exported_and_prefixed(source):
    assert "window.MediaViewer = MediaViewer;" in source
    # DOM 前缀是多页复用的唯一隔离手段：id 必须经前缀生成，不许硬编码 ms- 前缀
    assert "return `${this.prefix}-${name}`;" in source
    assert "this.prefix = prefix;" in source
