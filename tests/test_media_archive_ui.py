"""按号归档页的版式守护（frontend/js/components/media_archive.js）。

三个入口（按账号 / 登记已下载 / 粘贴链接）曾各占一张卡，目标根与子目录重复出现，
NAS 又单独占一整卡。这里固定「一张新建卡 + 一张任务卡」的结构，防止再拆回去。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "js" / "components" / "media_archive.js"


@pytest.fixture(scope="module")
def source():
    return PAGE.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_three_entries_share_one_create_card(source):
    create = _between(source, 'class="card ma-create-card"', 'id="ma-task-card"')
    for mode in ("account", "platform", "link"):
        assert f'data-arch-mode="{mode}"' in create, f"缺少 {mode} 分段"
    for cid in ("ma-platform-select", "ma-platform-archive", "ma-batch-create", "ma-fallback-fakeid"):
        assert f'id="{cid}"' in create, f"{cid} 应在新建成卡里"
    # 分段控件与切换方法
    assert 'id="ma-mode-tabs"' in create
    init = _between(source, "async init() {", "destroy() {")
    assert "this.setArchMode(btn.dataset.mode)" in init
    mode_fn = _between(source, "setArchMode(mode) {", "showProgress() {")
    assert "panel.dataset.archMode !== mode" in mode_fn


def test_progress_nas_and_jobs_share_one_task_card(source):
    task = _between(source, 'id="ma-task-card"', "</div>\n        `;")
    for cid in ("ma-progress-card", "ma-nas-block", "ma-jobs-list", "ma-jobs-summary", "ma-nas-summary"):
        assert f'id="{cid}"' in task, f"{cid} 应在任务卡里"
    # NAS 默认收起，靠任务卡头部的开关展开
    assert 'id="ma-nas-block" hidden' in task
    assert 'id="ma-nas-toggle"' in task
    init = _between(source, "async init() {", "destroy() {")
    assert "ma-nas-toggle" in init
