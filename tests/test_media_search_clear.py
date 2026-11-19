"""素材检索「重置全部」的行为守护（frontend/js/components/media_search.js）。

重置入口在筛选抽屉底部；用户预期是清空本页所有条件。曾经的实现只清文件框和
网址框两个输入，关键词、平台、账号、媒体来源、标签、NSFW 开关都留在原值上，
结果区也停在旧条件的结果里。这里把「清哪些」写成断言，防止后续改动悄悄缩减范围。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "js" / "components" / "media_search.js"


@pytest.fixture(scope="module")
def source():
    return PAGE.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_reset_button_runs_full_reset(source):
    handler = [line for line in source.splitlines() if "filterReset.addEventListener" in line]
    assert len(handler) == 1
    assert "this.clearAll()" in handler[0]


def test_image_remove_button_only_drops_query_image(source):
    """预览框里的「移除」不再是清空全部：它只清查询图，避免两个按钮语义重叠。"""
    handler = [line for line in source.splitlines() if "imageClear.addEventListener" in line]
    assert len(handler) == 1
    assert "this.clearImageSource()" in handler[0]
    assert "clearAll()" not in handler[0]


def test_clear_all_resets_every_input(source):
    body = _between(source, "async clearAll() {", "    fmtBytes(bytes) {")
    for element in (
        "ms-q",            # 关键词
        "ms-root",         # 媒体来源
        "ms-tag-filter",   # 标签
        "ms-nsfw-only",    # NSFW 开关
        "ms-dl-limit",     # 账号下载篇数
        "ms-dl-link",      # 账号下载主页链接
    ):
        assert f"getElementById('{element}')" in body, f"清空必须覆盖 {element}"
    # 查询图的文件框与网址框由 clearImageSource 负责，和回显、图片结果一起复位
    image_body = _between(source, "clearImageSource() {", "async clearAll() {")
    for element in ("ms-image", "ms-image-url"):
        assert f"getElementById('{element}')" in image_body
    assert "this.clearImageSource();" in body
    assert "this._platform = '';" in body
    assert "this._account = '';" in body
    assert "this._nsfwOnly = false;" in body


def test_clear_all_restores_default_result_list(source):
    body = _between(source, "async clearAll() {", "    fmtBytes(bytes) {")
    # 只清输入不清结果，列表会停在旧条件的结果上，看起来像没清干净
    assert "await this.search(true);" in body


def test_media_source_falls_back_to_default_root(source):
    root = _between(source, "defaultRootId() {", "async loadStats() {")
    assert "'articles-full'" in root
    assert "this._roots[0]" in root


def test_scan_only_controls_live_in_drawer(source):
    """媒体来源只服务「扫描」，不该和检索条件并排；NSFW 与标签收进筛选抽屉。"""
    drawer = _between(source, 'id="ms-filter-drawer"', 'id="ms-image-hits"')
    for element in ("ms-nsfw-only", "ms-tag-filter", "ms-root", "ms-filter-reset"):
        assert f'id="{element}"' in drawer, f"{element} 应在筛选抽屉里"
    # 扫描/下载记录/待复核是管理动作，收进 ⋯ 菜单
    menu = _between(source, 'id="ms-more-menu"', 'class="ms-scope-row"')
    for element in ("ms-nsfw-review", "ms-download-log", "ms-scan"):
        assert f'id="{element}"' in menu


def test_search_bar_keeps_one_row_of_entries(source):
    row = _between(source, 'class="ms-search-row"', 'class="ms-scope-row"')
    for element in ("ms-q", "ms-go", "ms-filter-open", "ms-image-toggle", "ms-more"):
        assert f'id="{element}"' in row
    # 图片入口默认收起，只留一个「以图找图」按钮
    assert 'id="ms-image-panel"' in source
    assert 'id="ms-image-panel" hidden' in source
