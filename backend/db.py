"""
数据库持久化适配模块
支持 SQLite (内置零依赖) 与 MySQL (通过 pymysql)，并兼容原 JSON 数据结构。
"""

import os
import json
import time
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from backend.config import (
    DATA_DIR, ACCOUNTS_FILE, DOWNLOAD_HISTORY_FILE,
    get_settings, load_json, save_json
)

logger = logging.getLogger("backend.db")

SQLITE_DB_PATH = DATA_DIR / "wechat_tools.db"

def get_db_config() -> Dict[str, Any]:
    """获取当前生效的数据库配置（环境变量优先，其次 app_settings.json）"""
    settings = get_settings()
    db_type = os.environ.get("DB_TYPE") or settings.get("db_type") or "sqlite"
    db_type = db_type.lower().strip()

    return {
        "type": db_type,
        "sqlite_path": os.environ.get("DB_SQLITE_PATH") or settings.get("db_sqlite_path") or str(SQLITE_DB_PATH),
        "host": os.environ.get("DB_HOST") or settings.get("db_host") or "127.0.0.1",
        "port": int(os.environ.get("DB_PORT") or settings.get("db_port") or 3306),
        "user": os.environ.get("DB_USER") or settings.get("db_user") or "root",
        "password": os.environ.get("DB_PASSWORD") or settings.get("db_password") or "",
        "name": os.environ.get("DB_NAME") or settings.get("db_name") or "wechat_tools",
    }


def get_connection(cfg: Optional[Dict[str, Any]] = None, allow_fallback: bool = True):
    """获取数据库连接（支持 sqlite3 与 pymysql）"""
    if cfg is None:
        cfg = get_db_config()

    db_type = cfg.get("type", "sqlite")
    if db_type == "mysql":
        try:
            import pymysql
            from pymysql.cursors import DictCursor
            conn = pymysql.connect(
                host=cfg["host"],
                port=cfg["port"],
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["name"],
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
            )
            return conn, "mysql"
        except Exception as e:
            if not allow_fallback:
                raise
            logger.warning(f"[DB] MySQL 连接失败 ({e})，安全降级至 SQLite")

    # 默认使用 SQLite
    sqlite_path = Path(cfg.get("sqlite_path", str(SQLITE_DB_PATH)))
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(sqlite_path), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn, "sqlite"


def init_db():
    """初始化数据库表结构并自动同步已有 JSON 数据"""
    try:
        conn, driver = get_connection()
    except Exception as e:
        logger.error(f"[DB] 数据库初始化失败: {e}")
        return False

    try:
        cur = conn.cursor()
        if driver == "sqlite":
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    fakeid TEXT PRIMARY KEY,
                    book_id TEXT,
                    nickname TEXT,
                    alias TEXT,
                    round_head_img TEXT,
                    signature TEXT,
                    service_type INTEGER DEFAULT 1,
                    added_time REAL,
                    extra_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    account TEXT,
                    link TEXT,
                    path TEXT,
                    success INTEGER DEFAULT 1,
                    time REAL,
                    error TEXT,
                    cover_url TEXT,
                    digest TEXT,
                    publish_time INTEGER DEFAULT 0,
                    has_markdown INTEGER DEFAULT 0,
                    has_pdf INTEGER DEFAULT 0,
                    review_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_dh_account ON download_history(account);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_dh_link ON download_history(link);
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_dh_success ON download_history(success);
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS article_list_cache (
                    cache_key TEXT PRIMARY KEY,
                    fakeid TEXT NOT NULL,
                    keyword TEXT NOT NULL DEFAULT '',
                    account_name TEXT DEFAULT '',
                    articles_json TEXT NOT NULL,
                    total INTEGER DEFAULT 0,
                    buffer_begin INTEGER DEFAULT 0,
                    buffer_can_continue INTEGER DEFAULT 1,
                    source TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_alc_fakeid_keyword
                ON article_list_cache(fakeid, keyword);
            """)
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    fakeid VARCHAR(128) PRIMARY KEY,
                    book_id VARCHAR(128),
                    nickname VARCHAR(255),
                    alias VARCHAR(255),
                    round_head_img TEXT,
                    signature TEXT,
                    service_type INT DEFAULT 1,
                    added_time DOUBLE,
                    extra_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS download_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(500),
                    account VARCHAR(255),
                    link TEXT,
                    path VARCHAR(1000),
                    success TINYINT DEFAULT 1,
                    time DOUBLE,
                    error TEXT,
                    cover_url TEXT,
                    digest TEXT,
                    publish_time BIGINT DEFAULT 0,
                    has_markdown TINYINT DEFAULT 0,
                    has_pdf TINYINT DEFAULT 0,
                    review_id VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_dh_account (account),
                    INDEX idx_dh_success (success)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS article_list_cache (
                    cache_key VARCHAR(300) PRIMARY KEY,
                    fakeid VARCHAR(128) NOT NULL,
                    keyword VARCHAR(255) NOT NULL DEFAULT '',
                    account_name VARCHAR(255) DEFAULT '',
                    articles_json LONGTEXT NOT NULL,
                    total BIGINT DEFAULT 0,
                    buffer_begin INT DEFAULT 0,
                    buffer_can_continue TINYINT DEFAULT 1,
                    source VARCHAR(64) DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_alc_fakeid_keyword (fakeid, keyword)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

        conn.commit()
        # 首次运行时自动从 json 同步存量数据
        _sync_from_json(conn, driver)
        conn.close()
        conn = None
        # 将 data/article_cache/*.json 存量快照迁入新表（已存在则不覆盖）
        migrate_article_cache_json()
        return True
    except Exception as e:
        logger.error(f"[DB] 初始化表结构异常: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def _sync_from_json(conn, driver):
    """如果数据库表为空，自动从本地 json 文件导入历史数据"""
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM accounts")
        row = cur.fetchone()
        cnt = row["cnt"] if isinstance(row, dict) or driver == "mysql" else row[0]
        if cnt == 0 and ACCOUNTS_FILE.exists():
            accounts = load_json(ACCOUNTS_FILE, [])
            for a in accounts:
                if not isinstance(a, dict) or not a.get("fakeid"):
                    continue
                placeholder = "%s" if driver == "mysql" else "?"
                cur.execute(
                    f"INSERT OR REPLACE INTO accounts (fakeid, book_id, nickname, alias, round_head_img, signature, service_type, added_time) "
                    f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
                    (
                        a.get("fakeid", ""),
                        a.get("bookId", ""),
                        a.get("nickname", ""),
                        a.get("alias", ""),
                        a.get("round_head_img", ""),
                        a.get("signature", ""),
                        a.get("service_type", 1),
                        a.get("added_time", time.time())
                    )
                )

        cur.execute("SELECT COUNT(*) AS cnt FROM download_history")
        row2 = cur.fetchone()
        cnt2 = row2["cnt"] if isinstance(row2, dict) or driver == "mysql" else row2[0]
        if cnt2 == 0 and DOWNLOAD_HISTORY_FILE.exists():
            history = load_json(DOWNLOAD_HISTORY_FILE, [])
            for h in history:
                if not isinstance(h, dict) or not h.get("title"):
                    continue
                placeholder = "%s" if driver == "mysql" else "?"
                cur.execute(
                    f"INSERT INTO download_history (title, account, link, path, success, time, error, cover_url, digest, publish_time, has_markdown, has_pdf, review_id) "
                    f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
                    (
                        h.get("title", ""),
                        h.get("account", ""),
                        h.get("link", ""),
                        h.get("path", ""),
                        1 if h.get("success") else 0,
                        h.get("time", time.time()),
                        h.get("error"),
                        h.get("cover_url", ""),
                        h.get("digest", ""),
                        h.get("publish_time", 0),
                        1 if h.get("has_markdown") else 0,
                        1 if h.get("has_pdf") else 0,
                        h.get("id") or h.get("review_id", "")
                    )
                )
        conn.commit()
    except Exception as e:
        logger.debug(f"[DB] JSON 存量迁移至数据库跳过或异常: {e}")


def get_article_list_cache(fakeid: str, keyword: str = "") -> Optional[Dict[str, Any]]:
    """读取指定 fakeid+keyword 的文章列表缓存；任何异常均返回 None。"""
    if not fakeid:
        return None
    try:
        conn, driver = get_connection()
        cur = conn.cursor()
        placeholder = "%s" if driver == "mysql" else "?"
        cur.execute(
            f"""SELECT cache_key, fakeid, keyword, account_name, articles_json,
                       total, buffer_begin, buffer_can_continue, source, updated_at
                FROM article_list_cache
                WHERE fakeid = {placeholder} AND keyword = {placeholder}
                LIMIT 1""",
            (fakeid, keyword or ""),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        try:
            articles = json.loads(row["articles_json"] or "[]")
        except Exception:
            articles = []
        if not isinstance(articles, list):
            articles = []
        return {
            "cache_key": row["cache_key"],
            "fakeid": row["fakeid"],
            "keyword": row["keyword"] or "",
            "account_name": row["account_name"] or "",
            "articles": articles,
            "total": int(row["total"] or 0),
            "buffer_begin": int(row["buffer_begin"] or 0),
            "buffer_can_continue": bool(row["buffer_can_continue"]),
            "source": row["source"] or "",
            "updated_at": str(row["updated_at"] or ""),
        }
    except Exception as e:
        logger.debug(f"[DB] 读取文章列表缓存失败: {e}")
        return None


def save_article_list_cache(
    fakeid: str,
    keyword: str,
    *,
    articles: List[Dict[str, Any]],
    account_name: str = "",
    total: int = 0,
    buffer_begin: int = 0,
    buffer_can_continue: bool = False,
    source: str = "upstream",
    updated_at: str = "",
) -> bool:
    """保存/整表替换文章列表缓存，保留原 created_at。"""
    if not fakeid or not isinstance(articles, list):
        return False
    keyword = keyword or ""
    cache_key = f"{fakeid}\x1f{keyword}"
    updated_at = updated_at or datetime.now().astimezone().isoformat(timespec="seconds")
    payload = json.dumps(articles, ensure_ascii=False, separators=(",", ":"))
    try:
        conn, driver = get_connection()
        cur = conn.cursor()
        placeholder = "%s" if driver == "mysql" else "?"
        params = (
            cache_key,
            fakeid,
            keyword,
            account_name or "",
            payload,
            int(total or 0),
            int(buffer_begin or 0),
            1 if buffer_can_continue else 0,
            source or "upstream",
            updated_at,
        )
        verb = "INSERT OR REPLACE" if driver == "sqlite" else "REPLACE INTO"
        cur.execute(
            f"""{verb} INTO article_list_cache
                (cache_key, fakeid, keyword, account_name, articles_json,
                 total, buffer_begin, buffer_can_continue, source, updated_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder})""",
            params,
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.debug(f"[DB] 写入文章列表缓存失败: {e}")
        return False


def migrate_article_cache_json() -> int:
    """将 data/article_cache/*.json 迁入数据库；相同 key 已有记录时跳过。"""
    cache_dir = DATA_DIR / "article_cache"
    if not cache_dir.exists():
        return 0
    migrated = 0
    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = load_json(path, {})
            if not isinstance(data, dict):
                continue
            fakeid = str(data.get("fakeid") or "").strip()
            articles = data.get("articles")
            if not fakeid or not isinstance(articles, list):
                continue
            deduped = []
            seen = set()
            for item in articles:
                if not isinstance(item, dict):
                    continue
                key = item.get("id") or item.get("link") or item.get("title")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                deduped.append(item)
            articles = deduped
            keyword = str(data.get("keyword") or "").strip()
            if get_article_list_cache(fakeid, keyword) is not None:
                continue
            exported_at = str(data.get("exported_at") or "").strip()
            if not exported_at:
                exported_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            saved = save_article_list_cache(
                fakeid,
                keyword,
                articles=articles,
                account_name=str(data.get("account_name") or ""),
                total=len(articles),
                buffer_begin=int(data.get("bufferBegin") or 0),
                buffer_can_continue=bool(data.get("bufferCanContinue")),
                source="legacy_json",
                updated_at=exported_at,
            )
            if saved:
                migrated += 1
        except Exception as e:
            logger.debug(f"[DB] 迁移文章缓存 JSON {path.name} 失败: {e}")
    return migrated


def is_article_downloaded(account_name: str, link: str, title: str) -> Tuple[bool, Optional[str]]:
    """查询指定文章是否已在数据库/本地下载成功"""
    if not link and not (title and account_name):
        return False, None

    try:
        conn, driver = get_connection()
        cur = conn.cursor()
        placeholder = "%s" if driver == "mysql" else "?"
        conditions = []
        params = []
        if link:
            conditions.append(f"link = {placeholder}")
            params.append(link)
        if title and account_name:
            conditions.append(f"(title = {placeholder} AND account = {placeholder})")
            params.extend([title, account_name])

        where_clause = " OR ".join(conditions)
        cur.execute(
            f"SELECT path FROM download_history WHERE ({where_clause}) AND success = 1 ORDER BY id DESC LIMIT 1",
            params
        )
        row = cur.fetchone()
        conn.close()
        if row:
            path_val = row["path"] if isinstance(row, dict) or driver == "mysql" else row[0]
            if path_val and Path(path_val).exists():
                return True, path_val
    except Exception as e:
        logger.debug(f"[DB] 查询已下载状态异常: {e}")

    # 回落至 JSON 历史记录双重校验
    history = load_json(DOWNLOAD_HISTORY_FILE, [])
    for h in history:
        if not isinstance(h, dict):
            continue
        h_link = h.get("link", "")
        h_title = h.get("title", "")
        h_path = h.get("path")
        is_match = False
        if link and h_link and h_link == link:
            is_match = True
        elif title and account_name and h_title == title and h.get("account") == account_name:
            is_match = True

        if is_match and h.get("success") and h_path and Path(h_path).exists():
            return True, h_path

    return False, None


def record_history(entry: Dict[str, Any]):
    """记录单条下载历史（同步写入数据库与 JSON，保障绝对向下兼容）"""
    try:
        conn, driver = get_connection()
        cur = conn.cursor()
        placeholder = "%s" if driver == "mysql" else "?"
        cur.execute(
            f"INSERT INTO download_history (title, account, link, path, success, time, error, cover_url, digest, publish_time, has_markdown, has_pdf, review_id) "
            f"VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})",
            (
                entry.get("title", ""),
                entry.get("account", ""),
                entry.get("link", ""),
                entry.get("path", ""),
                1 if entry.get("success") else 0,
                entry.get("time", time.time()),
                entry.get("error"),
                entry.get("cover_url", ""),
                entry.get("digest", ""),
                entry.get("publish_time", 0),
                1 if entry.get("has_markdown") else 0,
                1 if entry.get("has_pdf") else 0,
                entry.get("id") or entry.get("review_id", "")
            )
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[DB] 写入历史记录异常: {e}")


def test_db_connection(cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """测试指定数据库连接是否通畅"""
    try:
        conn, driver = get_connection(cfg, allow_fallback=False)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        conn.close()
        return True, f"{driver.upper()} 连接成功！"
    except Exception as e:
        return False, str(e)

