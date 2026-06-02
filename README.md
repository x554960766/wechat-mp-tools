# 微信公众号批量下载工具箱

利用公众号后台官方搜索 API，无需mitmproxy抓包，实现**文章列表获取 + 完整离线下载**。

```
wechat-mp-tools/
├── README.md                          # 本文件
├── wechat_mp_login.py                 # ① 扫码登录（只需运行一次）
├── wechat_mp_article_fetcher.py       # ② 获取文章列表（仅列表，不含正文）
└── wechat_mp_batch_downloader.py      # ③ 批量下载（列表 + 全文 + 离线资源）
    data/
    ├── wechat_mp_config.json          # 登录凭证（自动生成）
    └── articles_full/                 # 下载输出目录
        └── {公众号名}/
            ├── {文章标题}/            # 每篇文章一个文件夹
            │   ├── article.html       # 完整离线 HTML
            │   ├── media/            # 图片 + 视频
            │   └── metadata.json
            └── {公众号名}_{fakeid}.json  # 文章列表
```

---

## 快速开始

### 第一步：扫码登录（只需一次）

```bash
cd /Users/apple/Downloads/wechat-mp-tools
pip install playwright requests scrapling "scrapling[fetchers]"
python3 -m playwright install chromium chromium-headless-shell

python3 wechat_mp_login.py
```

浏览器弹出微信公众平台页面 → 用微信扫码 → 自动保存凭证到 `data/wechat_mp_config.json`

---

### 第二步：配置要下载的公众号

编辑 `wechat_mp_batch_downloader.py` 顶部的配置区：

```python
TARGET_ACCOUNTS  = ["潇湘晨报", "另一公众号名称"]   # ← 改成你的目标
MAX_ARTICLES    = 20             # 每个号最多下载篇数（0=不限制）
```

---

### 第三步：一键批量下载

```bash
python3 wechat_mp_batch_downloader.py
```

每篇文章输出到 `data/articles_full/{公众号名}/{文章标题}/`，包含：
- `article.html` — 完整离线 HTML（图片/视频已本地化）
- `media/` — 下载的图片和视频文件
- `metadata.json` — 元数据

---

## 各工具说明

| 工具 | 功能 | 使用场景 |
|------|------|---------|
| `wechat_mp_login.py` | 扫码登录，保存 cookie + token | 首次使用或凭证过期时运行一次 |
| `wechat_mp_article_fetcher.py` | 只获取文章列表（标题+链接） | 只需要文章标题列表，不下载正文 |
| `wechat_mp_batch_downloader.py` | **列表 + 全文 + 离线 HTML** | 日常批量下载，完整离线阅读 |

---

## 常见问题

**Q: 提示 cookie/token 失效？**
→ 重新运行 `python3 wechat_mp_login.py` 扫码刷新凭证

**Q: 凭证多久过期？**
→ 通常 1-3 天，建议隔几天重新扫码一次

**Q: 某些图片下载失败（400 错误）？**
→ 这些是微信播放器装饰图（如 poster、data-cover），不影响正文阅读，可忽略

**Q: 想只获取列表不下载正文？**
→ 运行 `wechat_mp_article_fetcher.py`，输出为 Markdown + JSON 列表

---

## 技术原理

利用微信公众平台后台 **"写文章 → 搜索其他公众号文章"** 的官方功能 API，用自己公众号后台账号的 cookie + token 即可查询任意公众号的文章列表，无需mitmproxy抓包或破解微信客户端。
