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
    # 概览里的四个数字都必须是可点入口，点完能到清单看对应文件。
    stats = _between(source, "_loadStats() {", "_onStatsClick(event)")
    for key in ("total", "flagged", "unreviewed"):
        assert f"data-stat=\"${{key}}\"" in stats or f"stat('{key}'" in stats
    # 最大文件没有排序参数，但 stats 会给出那一个文件的 root/relpath，据此精确定位，
    # 不能做成点了没反应的假按钮。
    assert "stat('biggest'" in stats
    assert "data-relpath=\"${this.esc(biggest.relpath)}\"" in stats
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
    # 最大文件按 root + 完整相对路径定位（q 是相对路径模糊匹配，整条路径只命中它自己）
    assert "set('files-root', act.dataset.root || '')" in drill
    assert "set('files-q', act.dataset.relpath || '')" in drill
    # 清单默认折叠，跳过去之前必须先展开，否则用户只看到一张卡片标题
    assert "this._jumpToCard('list', token || 'list');" in drill
    jump = _between(source, "_jumpToCard(key, token = key) {", "async _loadList() {")
    assert "this._setCardExpanded(key, true);" in jump
    assert "this._scrollToCard(key);" in jump


def test_every_card_has_a_collapse_toggle(source):
    # 卡片折叠是统一入口：每张卡都带 data-card，init 时装按钮并包一层 body
    for key in ("stats", "dupStats", "filters", "index", "probe", "review", "list", "dupGroups"):
        assert f'data-card="{key}"' in source, f"卡片 {key} 缺 data-card 标记"
    init = _between(source, "async init() {", "destroy() {")
    assert "this._initCards();" in init
    cards = _between(source, "_initCards() {", "_readCardState() {")
    assert "querySelectorAll('[data-card]')" in cards
    assert "btn.dataset.cardToggle = key" in cards
    assert "body.dataset.cardBody = key" in cards
    # 头部是 space-between，按钮与标题必须包成一组，否则右侧按钮会被挤到中间
    assert "card-head-main" in cards


def test_list_card_collapsed_by_default_and_state_persisted(source):
    # 一进页面摊开 50 行太长，清单默认折叠；其余卡片默认展开
    assert "_collapsedByDefault: ['list']" in source
    toggle = _between(source, "_setCardExpanded(key, expanded, persist = true) {", "_scrollToCard(key) {")
    assert "classList.toggle('is-collapsed', !expanded)" in toggle
    assert "body.hidden = !expanded" in toggle
    assert "setAttribute('aria-expanded'" in toggle
    # 折叠状态落在 localStorage，切页回来不重置
    assert "files:cardState" in source


def test_stats_click_delegated_on_stable_container(source):
    # 概览块每次刷新整体重渲染，点击只能委托到固定容器 #files-stats-body 上
    init = _between(source, "async init() {", "destroy() {")
    assert "getElementById('files-stats-body')" in init
    assert "addEventListener('click'" in init
    assert "this._onStatsClick(event)" in init


def test_duplicate_stats_tiles_jump_to_groups(source):
    # 「重复情况」卡的两个数字以前是死数字，现在都跳到下方分组明细；
    # 该卡内容整体重渲染，点击同样只能委托到固定容器 #files-dups-body。
    dups = _between(source, "_loadDuplicates() {", "_renderDuplicates() {")
    assert 'data-jump="dupGroups"' in dups
    assert "files-stat-link" in dups
    init = _between(source, "async init() {", "destroy() {")
    assert "getElementById('files-dups-body')" in init
    assert "this._onDupStatsClick(event)" in init
    jump = _between(source, "_onDupStatsClick(event) {", "_previewDup(sha")
    assert "this._jumpToCard(act.dataset.jump, 'jump:' + act.dataset.jump);" in jump


def test_dup_members_have_preview_thumbnail(source):
    # 重复组以前只有文字表格，看不出哪两张是一模一样的图；每行加缩略图，点开是灯箱。
    member = _between(source, "_dupMember(member) {", "_previewDup(sha")
    assert 'data-act="preview"' in member
    assert "this._thumbUrl(member)" in member
    assert "files-thumb-sm" in member
    # 两个表头都要有预览列，否则列错位
    assert source.count("<th>预览</th>") == 2
    # 灯箱里放的是同一摘要的所有副本，翻页即可对比
    preview = _between(source, "_previewDup(sha", "async _onDupClick(event) {")
    assert "member.kind === 'image'" in preview
    assert "this._openViewer(images" in preview
    # 展开全部加载到的副本必须并进 group.members，否则灯箱漏掉后面这些
    expand = _between(source, "async _onDupClick(event) {", "};\n\nwindow.FilesPage")
    assert "group.members = merged;" in expand


def test_repeat_click_on_same_entry_collapses_card(source):
    # 同一个数字连点两次要能收回折叠态，否则下钻是单向的：只会展开，收不回去。
    jump = _between(source, "_jumpToCard(key, token = key) {", "async _loadList() {")
    assert "this._lastJumpCard === token" in jump
    assert "this._setCardExpanded(key, false);" in jump
    # 「已入库文件」和「疑似敏感」都跳到清单，必须用 token 区分入口，
    # 否则点第二个会被当成重复点击而把清单收起来。
    drill = _between(source, "_onStatsClick(event) {", "async _loadList() {")
    assert "const token = act.dataset.stat" in drill
    assert "act.dataset.kind ? 'kind:' + act.dataset.kind" in drill
    # 手动点三角开合之后，重复点击判定必须失效，否则下一次点数字会先收起来
    toggle = _between(source, "_toggleCard(key) {", "_setCardExpanded(key, expanded")
    assert "this._lastJumpCard = null;" in toggle


def test_pager_accepts_page_number_input(source):
    # 一页 50 条、共 811 条时有 17 页，只能点「下一页」翻十几下不现实：页码框可直接输入跳转。
    assert 'id="files-page-input"' in source
    assert 'type="number"' in source
    init = _between(source, "async init() {", "destroy() {")
    assert "getElementById('files-page-input')" in init
    assert "this._goPage(Number(pageInput.value) - 1)" in init
    assert "event.key === 'Enter'" in init
    # 输入框必须跟着真实状态刷新，否则刷新清单后框里还是旧页码，用户会拿着它跳错
    load = _between(source, "_loadList() {", "    _renderList() {")
    assert "pageInput.value = String(this._page + 1)" in load
    assert "pageInput.max = String(totalPages)" in load
    # 越界钳制在 _goPage 里：输入 999 应该落到最后一页，而不是报错或空白
    go = _between(source, "_goPage(page) {", "this._loadList();")
    assert "Math.min(totalPages - 1, page)" in go
