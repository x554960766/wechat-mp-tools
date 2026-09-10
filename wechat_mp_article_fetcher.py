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




import json
import time
import re
import requests
from pathlib import Path
from datetime import datetime

# ============================================================
#  ⚡ 配置区
# ============================================================

# ── 目标公众号列表（名称 -> fakeid / __biz）─────────────────────
# fakeid 就是公众号的 __biz 参数（Base64 字符串）
TARGET_ACCOUNTS = {
    "潇湘晨报": "Mjk0MDY5NjMyMA==",
}

PAGE_SIZE    = 10    # 每次拉取篇数（官方限制最大20，建议10稳妥）
MAX_ARTICLES = 50    # 最多抓取篇数（0=不限制）

# ── 路径（动态，基于脚本所在目录）─────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
DATA_DIR    = SCRIPT_DIR / "data"
OUTPUT_DIR  = DATA_DIR / "wechat_articles"
CONFIG_FILE = DATA_DIR / "wechat_mp_config.json"
# ============================================================


def load_credentials():
    """读取凭证：优先读取 account_pool.json 中最新捕获的有效凭证"""
    from backend.cred_redact import mask_secret
    pool_file = DATA_DIR / "account_pool.json"
    if pool_file.exists():
        try:
            accounts = json.loads(pool_file.read_bytes().decode("utf-8-sig", errors="replace"))
            active = [a for a in accounts if a.get("status") == "active" and (a.get("token") or a.get("cookie_str"))]
            if active:
                acc = active[0]
                token = acc.get("token") or acc.get("appmsg_token", "")
                cookie_str = acc.get("cookie_str", "")
                print(f"  📂 已从账号池加载 PC 微信捕获凭证（token={mask_secret(token, 6)}）")
                return token, cookie_str, acc
        except Exception:
            pass

    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_bytes().decode("utf-8-sig", errors="replace"))
        token = cfg.get("token", "")
        cookie_str = cfg.get("cookie_str", "") or cfg.get("cookie", "")
        if token:
            print(f"  📂 已从配置文件加载凭证（token={mask_secret(token, 6)}）")
            return token, cookie_str, cfg

    print("\n⚠️ 账号池及配置文件中未找到有效凭证。")
    print("💡 快捷配置：您可以直接输入 Token 与 Cookie，或在 PC 微信中打开中转页获取。")
    print("=" * 60)
    try:
        token = input("1. 请输入 Token (或按 Enter 跳过): ").strip()
        cookie_str = input("2. 请输入 Cookie 字符串 (或按 Enter 跳过): ").strip()
        if token and cookie_str:
            cfg = {"token": token, "cookie_str": cookie_str, "save_time": time.time()}
            CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            from backend.account_pool import account_pool
            acc = account_pool.add_or_update({
                "token": token,
                "cookie_str": cookie_str,
                "nickname": "手动配置凭证",
                "save_time": time.time(),
            })
            print("✅ 凭证已成功保存！继续执行数据拉取...\n")
            return token, cookie_str, cfg
    except Exception:
        pass

    print("\n❌ 提示：请通过以下 2 种方式之一提供凭证：")
    print("   1. 运行配置工具：python3 wechat_mp_login.py")
    print("   2. 在 PC 微信中打开中转页：http://127.0.0.1:5200/api/auth/mp-relay\n")
    import sys
    sys.exit(1)


def fetch_page_via_appmsgpublish(
    cookie_str: str,
    token: str,
    fakeid: str,
    begin: int,
    count: int,
    keyword: str = "",
    acc: dict = None,
) -> dict:
    """
    调用微信客户端历史消息原生接口 (profile_ext?action=getmsg)
    API: GET https://mp.weixin.qq.com/mp/profile_ext
    """
    import urllib.parse, re
    from backend.config import get_default_wechat_ua
    ua = get_default_wechat_ua()
    url = "https://mp.weixin.qq.com/mp/profile_ext"
    clean_cookie = cookie_str.replace(", ", "; ")
    if "pass_ticket=" in clean_cookie:
        clean_cookie = re.sub(r'pass_ticket=([^;,\s]+)', lambda m: 'pass_ticket=' + m.group(1).replace('+', '%2B'), clean_cookie)
    raw_token = urllib.parse.unquote(token) if token else ""
    acc = acc or {}
    # 严格 biz 隔离：优先使用该公众号的专属会话凭证（key/pass_ticket 属于单号会话，跨号使用会被拒绝）
    biz_entry = (acc.get("biz_tokens") or {}).get(fakeid)
    poc_token = ""
    poc_sid = ""
    wxtoken = "777"
    if isinstance(biz_entry, dict):
        key = biz_entry.get("key") or ""
        pass_ticket = urllib.parse.unquote(biz_entry["pass_ticket"]) if biz_entry.get("pass_ticket") else ""
        poc_token = urllib.parse.unquote(biz_entry["poc_token"]) if biz_entry.get("poc_token") else ""
        poc_sid = biz_entry.get("poc_sid") or ""
        wxtoken = biz_entry.get("wxtoken") or "777"
    else:
        key = acc.get("key", "")
        pass_ticket = urllib.parse.unquote(acc.get("pass_ticket", "")) if acc.get("pass_ticket") else ""
        poc_token = urllib.parse.unquote(acc.get("poc_token", "")) if acc.get("poc_token") else ""
        poc_sid = acc.get("poc_sid") or ""
        wxtoken = acc.get("wxtoken") or "777"
    uin = acc.get("uin", "")

    if poc_sid and "poc_sid=" not in clean_cookie:
        clean_cookie = f"{clean_cookie}; poc_sid={poc_sid}" if clean_cookie else f"poc_sid={poc_sid}"

    import base64
    uin_str = str(uin).strip() if uin else ""
    if uin_str and uin_str.isdigit():
        uin_encoded = base64.b64encode(uin_str.encode()).decode()
    else:
        uin_encoded = uin_str

    headers = {
        "User-Agent": ua,
        "Referer": "https://mp.weixin.qq.com/",
        "Cookie": clean_cookie,
    }
    params = {
        "action": "getmsg",
        "__biz": fakeid,
        "f": "json",
        "offset": str(begin),
        "count": str(count),
        "is_ok": "1",
        "scene": "124",
        "uin": uin_encoded,
        "key": str(key) if key else "",
        "pass_ticket": str(pass_ticket) if pass_ticket else "",
        "wxtoken": str(wxtoken) if wxtoken else "777",
        "poc_token": str(poc_token) if poc_token else "",
        "x5": "0",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=25, verify=False, proxies={"http": None, "https": None})
    except Exception:
        from curl_cffi import requests as c_req
        resp = c_req.get(url, params=params, headers=headers, timeout=25, impersonate="chrome", verify=False, proxies={"http": None, "https": None})

    if resp.status_code != 200:
        raise RuntimeError(f"微信历史消息 API 错误 (HTTP {resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    ret = data.get("ret", 0)

    if ret in (-3, -4, -5, -6, 200003):
        raise RuntimeError(f"❌ 微信客户端凭证（Cookie/appmsg_token）已过期 (ret={ret}, errmsg={data.get('errmsg')})，请在 PC 微信打开任意文章或历史消息刷新！")
    elif ret == 200013:
        raise RuntimeError("❌ 触发微信历史消息接口频次控制（200013），请稍后再试！")
    elif ret != 0:
        err_msg = data.get("errmsg", f"未知错误(ret={ret})")
        raise RuntimeError(f"微信历史消息 API 错误: {err_msg}")

    articles = []
    msg_list_str = data.get("general_msg_list", "")
    if msg_list_str:
        try:
            msg_data = json.loads(msg_list_str)
            for msg in msg_data.get("list", []):
                comm_info = msg.get("comm_msg_info", {})
                pub_time = comm_info.get("datetime", 0)
                app_msg = msg.get("app_msg_ext_info", {})
                if app_msg and app_msg.get("title"):
                    link = app_msg.get("content_url", "").replace("\\/", "/")
                    if link.startswith("//"):
                        link = "https:" + link
                    articles.append({
                        "title": app_msg.get("title", ""),
                        "link": link,
                        "cover": app_msg.get("cover", ""),
                        "digest": app_msg.get("digest", ""),
                        "update_time": pub_time,
                    })
                    for sub in app_msg.get("multi_app_msg_item_list", []):
                        if sub.get("title"):
                            sub_link = sub.get("content_url", "").replace("\\/", "/")
                            if sub_link.startswith("//"):
                                sub_link = "https:" + sub_link
                            articles.append({
                                "title": sub.get("title", ""),
                                "link": sub_link,
                                "cover": sub.get("cover", ""),
                                "digest": sub.get("digest", ""),
                                "update_time": pub_time,
                            })
        except Exception as e:
            print(f"解析 general_msg_list 异常: {e}")

    return {
        "publish_page": json.dumps({
            "publish_list": [
                {
                    "publish_info": json.dumps({
                        "appmsgex": articles
                    })
                }
            ],
            "total_count": data.get("total_count", len(articles))
        })
    }
    if ret != 0:
        err_msg = base_resp.get("err_msg", "未知错误")
        raise RuntimeError(f"微信API错误 (ret={ret}): {err_msg}")

    return data


def parse_articles_from_response(data: dict) -> tuple[list[dict], int]:
    """
    从 appmsgpublish 返回中提取文章列表

    返回结构（两层 JSON 字符串嵌套）：
    {
      "base_resp": {"ret": 0, ...},
      "publish_page": '{"publish_list":[{...}], "total_count":150}'
    }
    → publish_page.publish_list[].publish_info（又是一层 JSON 字符串）:
      '{"appmsgex":[{title,link,cover,...}]}'
    """
    publish_page_str = data.get("publish_page", "")
    if not publish_page_str:
        return [], 0

    publish_page = json.loads(publish_page_str)
    publish_list = publish_page.get("publish_list", [])
    total_count = publish_page.get("total_count", 0)

    articles = []
    for item in publish_list:
        publish_info_str = item.get("publish_info", "")
        if not publish_info_str:
            continue
        publish_info = json.loads(publish_info_str)
        appmsgex = publish_info.get("appmsgex", [])
        for a in appmsgex:
            articles.append(normalize_article(a))

    return articles, total_count


def normalize_article(item: dict) -> dict:
    """将 appmsgpublish 返回的文章数据标准化"""
    return {
        "title":       item.get("title", ""),
        "link":        item.get("link", ""),
        "cover":       item.get("cover", ""),
        "digest":      item.get("digest", ""),
        "author":      item.get("author", ""),
        "update_time": item.get("update_time", item.get("create_time", 0)),
        "is_original": item.get("copyright_type", "0") != "0",
        "item_show_type": item.get("item_show_type", 0),
    }


def fetch_all_articles(
    token: str,
    cookie_str: str,
    fakeid: str,
    account_name: str,
    acc: dict = None,
) -> list[dict]:
    """
    完整流程：分页调用 appmsgpublish，拉取全部文章

    分页逻辑：begin 从 0 开始，每次 +count，直到返回列表为空
    """
    all_articles = []
    begin = 0

    print(f"  📡 正在获取 [{account_name}] 的文章列表（方式一：appmsgpublish）...")

    while True:
        # 检查上限
        if MAX_ARTICLES > 0 and len(all_articles) >= MAX_ARTICLES:
            all_articles = all_articles[:MAX_ARTICLES]
            print(f"\n  ⚡ 已达上限 {MAX_ARTICLES} 篇，停止抓取")
            break

        data = fetch_page_via_appmsgpublish(
            cookie_str, token, fakeid, begin, PAGE_SIZE, acc=acc
        )

        articles, total_count = parse_articles_from_response(data)

        if not articles:
            # 返回为空 → 全部拉完
            break

        all_articles.extend(articles)
        fetched = len(all_articles)
        target = min(fetched, MAX_ARTICLES) if MAX_ARTICLES > 0 else fetched
        total_msg = f"（总计 {total_count} 篇）" if total_count else ""
        print(f"  ⬇️  已获取 {target}/{total_count if total_count else '未知'} 篇", end="\r")

        if MAX_ARTICLES > 0 and fetched >= MAX_ARTICLES:
            all_articles = all_articles[:MAX_ARTICLES]
            print(f"\n  ⚡ 已达上限 {MAX_ARTICLES} 篇，停止抓取")
            break

        begin += PAGE_SIZE

        # 适当延迟，避免频率过高
        time.sleep(0.5)

    print(f"\n  ✅ [{account_name}] 共获取 {len(all_articles)} 篇文章")
    return all_articles


def save_results(account_name: str, fakeid: str, articles: list[dict]):
    """保存文章列表到 JSON 和 Markdown"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", account_name)

    # JSON（完整数据）
    json_path = OUTPUT_DIR / f"{safe_name}_{fakeid[:10]}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"account": account_name, "fakeid": fakeid, "articles": articles},
            f, ensure_ascii=False, indent=2,
        )
    print(f"  💾 JSON 已保存: {json_path}")

    # Markdown（简洁列表，含链接）
    md_path = OUTPUT_DIR / f"{safe_name}_{fakeid[:10]}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {account_name} — 文章列表\n\n")
        f.write(f"- 公众号 fakeid: `{fakeid}`\n")
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


# ──────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────

def main():
    print("🚀 微信公众号文章获取工具（方式一：appmsgpublish 后台接口）")
    print(f"   目标账号数 : {len(TARGET_ACCOUNTS)}")
    print(f"   输出目录   : {OUTPUT_DIR}")
    print(f"   配置文件   : {CONFIG_FILE}")
    print()

    token, cookie_str, acc_dict = load_credentials()

    for account_name, fakeid in TARGET_ACCOUNTS.items():
        print(f"🔍 处理: {account_name} (fakeid={fakeid[:20]}...)")

        try:
            articles = fetch_all_articles(
                token, cookie_str, fakeid, account_name, acc=acc_dict
            )
        except RuntimeError as e:
            print(f"  ❌ 获取失败: {e}")
            print()
            continue

        if articles:
            save_results(account_name, fakeid, articles)
        else:
            print(f"  ⚠️  [{account_name}] 没有文章")
        print()

    print("🎉 全部完成！")


if __name__ == "__main__":
    ensure_virtualenv()
    main()

