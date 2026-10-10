import os
import sys

def ensure_virtualenv():
    """检测当前是否运行在虚拟环境 venv312 中。
    如果不是，并且检测到本地存在 venv312，则自动使用 venv312 的 python 解释器重载当前脚本！
    """
    if getattr(sys, 'frozen', False):
        return
    project_root = os.path.dirname(os.path.abspath(__file__))
    if sys.platform == 'win32':
        venv_python = os.path.join(project_root, 'venv312', 'Scripts', 'python.exe')
    else:
        venv_python = os.path.join(project_root, 'venv312', 'bin', 'python')
    if os.path.exists(venv_python):
        current_exe = os.path.abspath(sys.executable)
        target_exe = os.path.abspath(venv_python)
        if current_exe != target_exe:
            args = [venv_python] + sys.argv
            os.execv(venv_python, args)

ensure_virtualenv()

import json
import time
import re
from pathlib import Path
from datetime import datetime
from backend.account_pool import account_pool
from backend.articles import _fetch_articles_page
from backend.accounts import search_accounts

# ============================================================
#  ⚡ 配置区
# ============================================================
TARGET_ACCOUNTS = {
    "人民日报": "MjM5MjAxNDM4MA==",
}

PAGE_SIZE    = 10    # 每次拉取篇数
MAX_ARTICLES = 50    # 最多抓取篇数（0=不限制）

SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
OUTPUT_DIR  = DATA_DIR / "wechat_articles"
# ============================================================

def fetch_all_articles(account_name: str, fakeid: str) -> list[dict]:
    all_articles = []
    begin = 0

    print(f"  📡 正在通过微信读书接口获取 [{account_name}] 的文章列表...")

    while True:
        if MAX_ARTICLES > 0 and len(all_articles) >= MAX_ARTICLES:
            all_articles = all_articles[:MAX_ARTICLES]
            print(f"\n  ⚡ 已达上限 {MAX_ARTICLES} 篇，停止抓取")
            break

        count = PAGE_SIZE
        if MAX_ARTICLES > 0 and len(all_articles) + count > MAX_ARTICLES:
            count = MAX_ARTICLES - len(all_articles)

        try:
            page_articles, total_count, can_continue = _fetch_articles_page(
                fakeid=fakeid,
                begin=begin,
                count=count,
                keyword="",
            )
        except Exception as e:
            print(f"\n  ❌ 请求第 {begin} 条出错: {e}")
            break

        if not page_articles:
            break

        all_articles.extend(page_articles)
        fetched = len(all_articles)
        print(f"  ⬇️  已获取 {fetched}/{total_count if total_count else '未知'} 篇", end="\r")

        if MAX_ARTICLES > 0 and fetched >= MAX_ARTICLES:
            all_articles = all_articles[:MAX_ARTICLES]
            print(f"\n  ⚡ 已达上限 {MAX_ARTICLES} 篇，停止抓取")
            break

        if len(page_articles) < count or (total_count and fetched >= total_count):
            break

        begin += len(page_articles)
        time.sleep(0.3)

    print(f"\n  ✅ [{account_name}] 共获取 {len(all_articles)} 篇文章")
    return all_articles


def save_results(account_name: str, fakeid: str, articles: list[dict]):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", account_name)

    json_path = OUTPUT_DIR / f"{safe_name}_{fakeid[:10]}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"account": account_name, "fakeid": fakeid, "articles": articles},
            f, ensure_ascii=False, indent=2,
        )
    print(f"  💾 JSON 已保存: {json_path}")

    md_path = OUTPUT_DIR / f"{safe_name}_{fakeid[:10]}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {account_name} — 文章列表\n\n")
        f.write(f"- 公众号 fakeid / __biz: `{fakeid}`\n")
        f.write(f"- 获取时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"- 共 {len(articles)} 篇文章\n\n")
        f.write("---\n\n")
        for i, a in enumerate(articles, 1):
            title   = a.get("title", "")
            link    = a.get("link", "")
            cover   = a.get("cover", "")
            digest  = a.get("digest", "")
            author  = a.get("author", "")
            ctime   = a.get("update_time", 0)
            date_str = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d") if ctime else ""
            f.write(f"{i}. **{title}**  {date_str}")
            if author:
                f.write(f"  @{author}")
            f.write(f"\n")
            if digest:
                f.write(f"   > {digest}\n")
            if cover:
                f.write(f"   ![封面]({cover})\n")
            f.write(f"   {link}\n\n")
    print(f"  📝 Markdown 已保存: {md_path}")
    return json_path, md_path


def main():
    print("🚀 微信公众号文章获取工具（微信读书协议版）")
    active_accs = account_pool.get_active_accounts()
    if not active_accs:
        print("❌ 当前账号池无有效微信读书账号！请先运行 python3 wechat_mp_login.py 扫码登录。")
        sys.exit(1)

    print(f"   账号池状态: {len(active_accs)} 个可用账号 (主账号: {active_accs[0].get('nickname')})")
    print(f"   目标账号数: {len(TARGET_ACCOUNTS)}")
    print(f"   输出目录  : {OUTPUT_DIR}\n")

    for account_name, fakeid in TARGET_ACCOUNTS.items():
        print(f"🔍 处理: {account_name} (fakeid={fakeid})")
        # 如果 fakeid 为空，先搜索
        if not fakeid:
            res = search_accounts(account_name)
            if res.get("list"):
                fakeid = res["list"][0]["fakeid"]
                print(f"  🎯 自动匹配到公众号 fakeid: {fakeid}")
            else:
                print(f"  ❌ 未找到公众号 {account_name}")
                continue

        try:
            articles = fetch_all_articles(account_name, fakeid)
        except Exception as e:
            print(f"  ❌ 获取失败: {e}\n")
            continue

        if articles:
            save_results(account_name, fakeid, articles)
        else:
            print(f"  ⚠️  [{account_name}] 没有文章")
        print()

    print("🎉 全部完成！")


if __name__ == "__main__":
    main()
