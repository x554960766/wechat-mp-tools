"""全局文件检索页（frontend/js/components/files.js）的手动加索引与 NSFW 不变量守护。

后端扫描与 NSFW 能力已存在于媒体服务，本页只负责交互。这几条不变量写在测试上而不是注释里，
防止后续改动把「筛选条件是否真的传给后端」「复核是否走 second 级结论」「行内徽标是否只对图片出现」
悄悄改掉：

1. 清单查询必须把 NSFW 筛选条件透传给 ``API.files.list``；
2. 待复核列表必须取 ``include_reviewed=false``（已复核的出列）；
3. 复核必须调用 ``API.media.nsfwReview`` 并刷新统计、清单与待复核；
4. 行内 NSFW 徽标只对图片生效，避免给文本/视频行挂无意义的角标；
5. 页面内 id 必须唯一——NSFW 筛选下拉与复核面板曾共用 ``files-nsfw``，导致
   ``getElementById`` 命中下拉、复核列表被写进下拉里而面板恒空（2026-11-18 修复）。
"""
from pathlib import Path

import re

import pytest

PAGE = Path(__file__).resolve().parents[1] / "frontend" / "js" / "components" / "files.js"


@pytest.fixture(scope="module")
def source():
    return PAGE.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_list_passes_nsfw_filter_to_api(source):
    load = _between(source, "_loadList() {", "_renderList()")
    assert "nsfw: (document.getElementById('files-nsfw')" in load
    assert "API.files.list(params" in load


def test_filter_control_and_reset_exist(source):
    # 检索条件里要有 NSFW 下拉，重置必须清空它，否则重置后残留筛选会与界面不一致
    assert '<select class="form-input" id="files-nsfw">' in source
    assert '<option value="flagged">' in source
    assert '<option value="unreviewed">' in source
    reset = _between(source, "_reset() {", "this._search();")
    assert "document.getElementById('files-nsfw')" in reset


def test_nsfw_panel_uses_dedicated_id(source):
    # 复核面板容器必须是独立 id，且绑定/渲染/加载都指向它，不能与筛选下拉共用 files-nsfw
    assert 'id="files-nsfw-panel"' in source
    load = _between(source, "_loadNsfw() {", "_renderNsfw()")
    assert "document.getElementById('files-nsfw-panel')" in load
    render = _between(source, "_renderNsfw() {", "_onNsfwClick(event)")
    assert "document.getElementById('files-nsfw-panel')" in render


def test_render_template_has_unique_ids(source):
    # 页面内 id 唯一：getElementById 只能命中首个同名元素，撞名会让面板写进别的容器而恒空
    render = _between(source, "render() {", "async init()")
    ids = re.findall(r'id="([^"]+)"', render)
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    assert not duplicated, f"render 模板存在重复 id: {duplicated}"



def test_nsfw_panel_requests_unreviewed_only(source):
    load = _between(source, "_loadNsfw() {", "_renderNsfw()")
    assert "API.media.nsfwFlagged({ include_reviewed: false" in load


def test_review_calls_api_and_refreshes(source):
    review = _between(source, "_reviewNsfw(root, relpath, label) {", "Toast.error('复核失败")
    assert "API.media.nsfwReview({ root, relpath, label, reviewed_by" in review
    assert "_loadStats(), this._loadList(), this._loadNsfw()" in review


def test_row_badge_only_for_images(source):
    badge = _between(source, "_nsfwBadge(item) {", "render() {")
    assert "item.kind !== 'image'" in badge
    assert "item.nsfw.label !== 'nsfw'" in badge


def test_viewer_review_handler_wired(source):
    open = _between(source, "_openViewer(images, index, pano = false) {", "_goPage(page)")
    assert "getNsfw: (image) => image.nsfw || null" in open
    assert "API.media.nsfwReview({" in open
    assert "reviewed_by: '本机'" in open


def test_global_nsfw_scan_wired(source):
    # 全局检测：发起、轮询、覆盖率提示三处都要真的接上接口，且离开页面时清掉轮询定时器，
    # 否则换页后仍会持续打 /api/nsfw/scans/{id}。
    assert 'id="files-nsfw-scan"' in source
    assert 'id="files-nsfw-rescan"' in source
    trigger = _between(source, "_triggerNsfwScan(force) {", "_pollNsfwScan(jobId) {")
    assert "API.media.nsfwScan({ roots: [], force })" in trigger
    assert "Modal.confirm('全部重检'" in trigger
    poll = _between(source, "_pollNsfwScan(jobId) {", "_refreshNsfwStatus() {")
    assert "API.media.nsfwScanStatus(jobId)" in poll
    status = _between(source, "_refreshNsfwStatus() {", "// ── NSFW 复核")
    assert "API.media.nsfwStatus({ showError: false })" in status
    assert "data.pending" in status
    destroy = _between(source, "destroy() {", "async _loadRoots() {")
    assert "_nsfwTimer" in destroy


def test_stats_tiles_are_drill_down_entries(source):
    # 「已入库文件 / 疑似敏感 / 待复核」三个数字必须是可点入口，点完能到清单看这批文件；
    # 「最大文件」没有排序参数可传，不做成点了没反应的假按钮。
    stats = _between(source, "_loadStats() {", "_onStatsClick(event)")
    for key in ("total", "flagged", "unreviewed"):
        assert f"data-stat=\"${{key}}\"" in stats or f"stat('{key}'" in stats
    assert 'data-stat="biggest"' not in source
    assert "files-stat-link" in stats
    # 按类型/按媒体根的标签同样可以下钻，否则用户看到「图片 761」却点不进去
    assert 'data-kind="${this.esc(k.kind)}"' in stats
    assert 'data-root="${this.esc(r.root)}"' in stats


def test_stats_click_clears_filters_and_scrolls(source):
    # 每次下钻先清空全部条件，避免和上一次筛选叠加成空结果；下钻后必须滚到清单，
    # 否则清单在页面下方，用户会以为点了没反应。
    drill = _between(source, "_onStatsClick(event) {", "async _loadList() {")
    for cid in ("files-q", "files-ext", "files-dup", "files-kind", "files-root", "files-nsfw"):
        assert f"set('{cid}', '')" in drill, f"下钻前未清空 {cid}"
    assert "set('files-nsfw', 'flagged')" in drill
    assert "set('files-nsfw', 'unreviewed')" in drill
    assert "set('files-kind', act.dataset.kind)" in drill
    assert "set('files-root', act.dataset.root)" in drill
    assert "this._search();" in drill
    assert "scrollIntoView" in drill


def test_stats_click_delegated_on_stable_container(source):
    # 概览块每次刷新整体重渲染，点击只能委托到固定容器 #files-stats-body 上
    init = _between(source, "async init() {", "destroy() {")
    assert "getElementById('files-stats-body')" in init
    assert "addEventListener('click'" in init
    assert "this._onStatsClick(event)" in init
