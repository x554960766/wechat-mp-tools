"""素材检索页悬停预览（frontend/js/components/media_search.js）的不变量守护。

结果区每次检索都整体重渲染，预览浮层又挂在 document 上，最容易悄悄坏掉的是
「卡片有没有带上预览地址」「浮层会不会残留监听」「视频是不是静音自动播放」
这几件事。它们都在这里写成断言：

1. 三类卡片（内容条目、图片命中、视频画面命中）都要带预览地址；
2. 预览用 display=1 的大图，不是 360 缩略图——缩略图放大后必然糊；
3. 视频浮层必须 muted 才能自动播放，否则浏览器直接拒绝；
4. 页面卸载要解绑 document 监听并移除浮层，否则换页后每张卡片仍会拉起浮层；
5. 触屏没有 hover，CSS 必须彻底隐藏浮层，避免点按后残留一块遮罩。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "js" / "components" / "media_search.js"
STYLE = ROOT / "frontend" / "css" / "style.css"


@pytest.fixture(scope="module")
def source():
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style():
    return STYLE.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_content_card_carries_cover_preview(source):
    results = _between(source, "renderResults(err) {", "box.querySelectorAll('.content-card')")
    # 视频条目没有图片封面，预览走抽帧封面附带的播放入口；图片条目仍用 display=1 大图
    assert "const previewVideo = item.cover && item.cover.video" in results
    assert 'data-preview-video="${esc(previewVideo)}"' in results
    assert "item.cover.file" in results


def test_content_video_preview_hint_matches_click_action(source):
    show = _between(source, "_showPreview(card, src, x, y) {", "_sizePreview(node, x, y) {")
    assert "card.classList.contains('content-card')" in show
    assert "'视频预览 · 点击查看详情'" in show


def test_image_cards_preview_full_size_not_thumb(source):
    cards = [line for line in source.splitlines() if "ms-image-card" in line and "data-preview-image" in line]
    assert len(cards) == 2, "图片命中与按标签浏览两处卡片模板都要带预览地址"
    for line in cards:
        assert "item.file || item.thumb" in line


def test_video_hit_card_carries_stream_url(source):
    hits = _between(source, "renderVideoHits(groups) {", "const empty = document.querySelector")
    assert 'data-preview-video="${esc(group.file ? \'/media\' + group.file : \'\')}"' in hits


def test_hover_layer_is_singleton_and_uses_event_delegation(source):
    init = _between(source, "_initHoverPreview() {", "_teardownHoverPreview() {")
    assert "if (this._hoverPreview) return;" in init
    assert "box.className = 'hover-preview';" in init
    assert "document.body.appendChild(box);" in init
    # 结果区会被整体重渲染，逐卡片绑监听会随 innerHTML 一起丢掉
    assert "document.addEventListener('mouseover', onOver);" in init
    assert "closest('[data-preview-image],[data-preview-video]')" in init


def test_video_preview_is_muted_and_cleaned_up(source):
    show = _between(source, "_showPreview(card, src, x, y) {", "_sizePreview(node, x, y) {")
    assert "node.muted = true;" in show
    assert "node.loop = true;" in show
    reset = _between(source, "_hoverMediaReset(box) {", "_showPreview(card, src, x, y) {")
    assert "video.pause();" in reset
    assert "video.removeAttribute('src');" in reset


def test_destroy_removes_document_listeners_and_layer(source):
    destroy = _between(source, "destroy() {", "// ── 平台 / 账号筛选")
    assert "this._teardownHoverPreview();" in destroy
    teardown = _between(source, "_teardownHoverPreview() {", "_hoverMediaReset(box) {")
    for event in ("mouseover", "mouseout", "mousemove", "scroll", "wheel"):
        assert f"document.removeEventListener('{event}', bound." in teardown
    assert "this._hoverPreview.remove();" in teardown


def test_preview_width_follows_media_ratio(source):
    size = _between(source, "_sizePreview(node, x, y) {", "_placePreview(x, y) {")
    assert "node.naturalWidth || node.videoWidth" in size
    assert "Math.min(MAX_W / width, MAX_H / height, 1)" in size


def test_touch_devices_hide_layer(style):
    block = style[style.index(".hover-preview {"):]
    assert "@media (hover: none)" in block
    touch = _between(block, "@media (hover: none) {", "}")
    assert ".hover-preview" in touch
    assert "display: none !important;" in touch
