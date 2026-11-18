"""全局工作台「内容审核（NSFW）」配置卡（frontend/js/components/settings.js）不变量守护。

这张卡是唯一能把阈值/鉴黄方案改到 settings.json 的入口，写错的代价是「扫描照跑但口径错了」，
用户很难发现。以下几条不变量因此写在测试里而不是注释里：

1. 锁定字段（部署环境变量覆盖）在保存时必须**不出现在 payload 里**——发了必然 409，用户看到的是
   「保存失败」而不是「这项被部署锁定」，提示会指向错误的层；
2. 换方案后必须隐藏「重算」入口——旧分数来自另一个模型，重算等于用新阈值重判别人的分数；
3. 三档预设值必须与后端 ``NSFW_THRESHOLD_PRESETS`` 一致，防前后端各改一处造成静默漂移；
4. ``init()`` 与 ``onShow()`` 都要重新拉配置，否则从别的页切回来看到的是过期值；
5. 卡片内 id 必须唯一——这类面板曾因 id 撞车把内容写进另一个控件（见 test_files_page.py 第 5 条）。
"""
from pathlib import Path

import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "frontend" / "js" / "components" / "settings.js"
ROUTES = ROOT / "media_service" / "app" / "media_routes.py"


@pytest.fixture(scope="module")
def source():
    return PAGE.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


def test_card_controls_exist_once(source):
    for element_id in (
        "nsfw-settings-card",
        "nsfw-auto-scan",
        "nsfw-threshold-input",
        "nsfw-provider-select",
        "nsfw-provider-hint",
        "nsfw-stale-banner",
        "nsfw-stale-text",
        "nsfw-coverage-line",
    ):
        assert source.count(f'id="{element_id}"') == 1, f"{element_id} 必须唯一且存在于卡片内"
    # 只数 radio 元素本身；JS 里 querySelectorAll 的选择器字符串不算
    assert source.count('<input type="radio" name="nsfw-threshold-preset"') == 4


def test_page_refetches_settings_on_init_and_show(source):
    init_block = _between(source, "async init()", "async onShow()")
    show_block = _between(source, "async onShow()", "async loadNsfwSettings()")
    assert "await this.loadNsfwSettings();" in init_block
    assert "await this.loadNsfwSettings();" in show_block


def test_locked_fields_are_omitted_from_payload(source):
    save = _between(source, "async saveNsfwSettings()", "async recomputeNsfw()")
    # 两个受环境变量锁定的字段都必须先判 locked 再决定是否进 payload
    assert "if (data && !data.locked.auto_scan)" in save
    assert "if (data && !data.locked.threshold)" in save
    assert "API.media.nsfwSettingsSave(payload)" in save
    # provider 没有环境变量覆盖，始终可提交
    assert "payload.provider = document.getElementById('nsfw-provider-select').value;" in save


def test_recompute_hidden_after_provider_switch(source):
    render = _between(source, "renderNsfwSettings(data) {", "onNsfwThresholdPreset(el) {")
    assert "if (data.stale.provider)" in render
    assert "banner.querySelector('button').style.display = 'none';" in render
    assert "只能重扫、不能重算" in render
    recompute = _between(source, "async recomputeNsfw()", "\n};")
    assert "API.media.nsfwRecompute()" in recompute


def test_preset_values_match_backend(source):
    routes = ROUTES.read_text(encoding="utf-8")
    backend = {
        match.group(1): float(match.group(2))
        for match in re.finditer(
            r'\{"id": "(\w+)", "label": "[^"]+", "threshold": ([0-9.]+)\}', routes
        )
    }
    assert backend == {"loose": 0.65, "standard": 0.5, "strict": 0.35}, "后端预设值变了，前端必须同步"

    block = _between(source, "nsfwPresetValues:", "async loadNsfwSettings()")
    frontend = {
        match.group(1): float(match.group(2))
        for match in re.finditer(r"(\w+): ([0-9.]+)", block)
    }
    assert frontend == backend
