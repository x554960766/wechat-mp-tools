"""SQLite 持久化与不可变检索快照。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import config

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS index_meta(id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL, encoder_fingerprint TEXT NOT NULL, encoder_spec_json TEXT NOT NULL, root_bindings_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS articles(article_id TEXT PRIMARY KEY,title TEXT NOT NULL,source_url TEXT NOT NULL,published_at TEXT,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS images(id INTEGER PRIMARY KEY,root TEXT NOT NULL,relpath TEXT NOT NULL,size INTEGER NOT NULL,mtime REAL NOT NULL,content_sha256 TEXT,content_version TEXT NOT NULL,emb BLOB,article_id TEXT REFERENCES articles(article_id),shot_at TEXT,ingested_at TEXT,indexed_at TEXT,origin TEXT NOT NULL CHECK(origin IN ('archive','scan','legacy_import')),state TEXT NOT NULL CHECK(state IN ('ready','quarantined','deleted')),last_error TEXT,UNIQUE(root,relpath),CHECK((state='ready' AND emb IS NOT NULL AND length(emb)=2048 AND ingested_at IS NOT NULL AND indexed_at IS NOT NULL) OR (state<>'ready' AND emb IS NULL)));
CREATE INDEX IF NOT EXISTS idx_images_root ON images(root);
CREATE INDEX IF NOT EXISTS idx_images_ingested ON images(ingested_at);
CREATE TABLE IF NOT EXISTS contents(content_id TEXT PRIMARY KEY,root TEXT NOT NULL,relpath TEXT NOT NULL,platform TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,account TEXT,url TEXT,published_at TEXT,digest TEXT,body TEXT,cover_relpath TEXT,media_count INTEGER NOT NULL DEFAULT 0,observed_at TEXT,content_sha256 TEXT NOT NULL,work_key TEXT,indexed_at TEXT NOT NULL,UNIQUE(root,relpath));
CREATE INDEX IF NOT EXISTS idx_contents_root ON contents(root);
CREATE INDEX IF NOT EXISTS idx_contents_platform ON contents(platform);
CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(content_id UNINDEXED,title,body,account,tokenize='unicode61');
-- 标签：作品标签按 work_key（平台+稳定作品 ID）共享，文件标签按 content_sha256 共享。
-- 两者都让同版本（同内容摘要）归档副本共享标签与人工复核结论（Spec §4.3）。
-- work_key 缺失时回退到 content_id，即该条目只在本地位置可见标签。
CREATE TABLE IF NOT EXISTS tags(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS image_tags(root TEXT NOT NULL,relpath TEXT NOT NULL,tag TEXT NOT NULL,content_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(root,relpath,tag));
CREATE INDEX IF NOT EXISTS idx_image_tags_sha ON image_tags(content_sha256);
CREATE TABLE IF NOT EXISTS content_tags(content_id TEXT NOT NULL,tag TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(content_id,tag));
-- 视频代表帧：同一中文-CLIP 向量，按文件归并命中帧；带时间戳供跳转播放。
CREATE TABLE IF NOT EXISTS videos(root TEXT NOT NULL,video_relpath TEXT NOT NULL,content_sha256 TEXT NOT NULL,duration REAL,frame_count INTEGER,sampled_at TEXT NOT NULL,PRIMARY KEY(root,video_relpath,content_sha256));
CREATE TABLE IF NOT EXISTS video_frames(id INTEGER PRIMARY KEY AUTOINCREMENT,root TEXT NOT NULL,video_relpath TEXT NOT NULL,content_sha256 TEXT NOT NULL,ts_seconds REAL NOT NULL,frame_sha256 TEXT,emb BLOB,created_at TEXT NOT NULL,UNIQUE(root,video_relpath,ts_seconds));
CREATE INDEX IF NOT EXISTS idx_frames_video ON video_frames(root,video_relpath,content_sha256);
-- 两级 NSFW：初筛/精筛分别落库；人工复核结论按文件内容版本（content_sha256）保存，重扫/换模型保留。
CREATE TABLE IF NOT EXISTS nsfw_results(id INTEGER PRIMARY KEY AUTOINCREMENT,root TEXT NOT NULL,relpath TEXT NOT NULL,content_sha256 TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('image','video','frame')),ts_seconds REAL,stage TEXT NOT NULL CHECK(stage IN ('first','second')),nsfw_type TEXT,score REAL,label TEXT,status TEXT NOT NULL,model_version TEXT,reviewed_by TEXT,reviewed_at TEXT,created_at TEXT NOT NULL,UNIQUE(root,relpath,content_sha256,kind,stage,ts_seconds));
CREATE INDEX IF NOT EXISTS idx_nsfw_file ON nsfw_results(root,relpath,content_sha256);
-- 独立文本（非内容条目正文的 TXT/MD/HTML）全文检索。
CREATE TABLE IF NOT EXISTS standalone_texts(id INTEGER PRIMARY KEY AUTOINCREMENT,root TEXT NOT NULL,relpath TEXT NOT NULL,content_sha256 TEXT NOT NULL,kind TEXT,title TEXT,body TEXT,indexed_at TEXT NOT NULL,UNIQUE(root,relpath));
CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(text_id UNINDEXED,title,body,tokenize='unicode61');
"""

# 内容条目的 FTS 列存的是切好词的空格串（见 contents.tokenize）；这里不建 content= 外部
# 内容表，因为那样删除时要用 FTS5 的 'delete' 指令回填旧值，容易和真值不一致。

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

class Store:
    def __init__(self, path: Path | str = config.INDEX_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.execute("PRAGMA user_version=3")
        self.conn.commit()
        from .encoder import fingerprint
        self.set_fingerprint(fingerprint(), {"model_id": config.MODEL_ID, "revision": config.MODEL_REVISION, "dimension": 512, "dtype": "float32", "normalize": "l2"})
        self._snapshot = (0, [], np.empty((0, 512), dtype=np.float32))
        # 视频代表帧单独一份矩阵：帧数量远小于图片，仍按代际缓存，检索时不再逐条读库。
        self._frame_snapshot = ([], np.empty((0, 512), dtype=np.float32))
        self.reload_snapshot()
        self.reload_frame_snapshot()

    def _migrate(self):
        """把旧库补齐到当前结构；已有列一律不动，避免覆盖线上数据。"""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(images)")}
        if "content_id" not in columns:
            self.conn.execute("ALTER TABLE images ADD COLUMN content_id TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_images_content ON images(content_id)")
        content_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(contents)")}
        if "work_key" not in content_columns:
            self.conn.execute("ALTER TABLE contents ADD COLUMN work_key TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_contents_work_key ON contents(work_key)")

    def _commit(self, sql: str, params=()):
        with self.lock:
            self.conn.execute(sql, params)
            self.conn.commit()
            self.reload_snapshot()

    def reload_snapshot(self):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM images WHERE state='ready' ORDER BY root,relpath").fetchall()
            meta = self.conn.execute("SELECT generation FROM index_meta WHERE id=1").fetchone()
            records=[]; vectors=[]
            for row in rows:
                vec=np.frombuffer(row["emb"], dtype=np.float32)
                if vec.size == 512 and np.isfinite(vec).all():
                    records.append(dict(row)); vectors.append(vec.copy())
            matrix=np.vstack(vectors).astype(np.float32) if vectors else np.empty((0,512),dtype=np.float32)
            matrix.setflags(write=False)
            self._snapshot=(int(meta[0]) if meta else 0, records, matrix)

    def snapshot(self):
        return self._snapshot

    def set_fingerprint(self, fingerprint: str, spec: dict | None = None):
        with self.lock:
            row=self.conn.execute("SELECT encoder_fingerprint,root_bindings_json FROM index_meta WHERE id=1").fetchone()
            if row and row[0] != fingerprint:
                count = self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
                if count:
                    raise RuntimeError("encoder_mismatch: 需停服后全库重建")
                self.conn.execute("DELETE FROM index_meta WHERE id=1")
                row = None
            bindings={r["id"]:{"type":r["type"],"path":r["path"],"exclude_paths":r.get("exclude_paths",[])} for r in config.MEDIA_ROOTS}
            if row:
                old_bindings=json.loads(row["root_bindings_json"])
                for root_id, old in old_bindings.items():
                    current=bindings.get(root_id)
                    old={"type":old["type"],"path":old["path"],"exclude_paths":old.get("exclude_paths",[])}
                    if current != old:
                        count=self.conn.execute("SELECT COUNT(*) FROM images WHERE root=?",(root_id,)).fetchone()[0]
                        if count:
                            raise RuntimeError("root_binding_mismatch: 已索引媒体根绑定变化，需停服核实并重建")
                self.conn.execute("UPDATE index_meta SET root_bindings_json=? WHERE id=1",
                                  (json.dumps(bindings,sort_keys=True),))
            else:
                self.conn.execute("INSERT INTO index_meta VALUES(1,0,?,?,?)",
                                  (fingerprint,json.dumps(spec or {},sort_keys=True),json.dumps(bindings,sort_keys=True)))
            self.conn.commit()

    def add_image(self, root: str, relpath: str, data: bytes, *, article=None, origin="archive", size=None, mtime=None, embedding=None, shot_at=None):
        import hashlib
        from .media import validate_relpath
        config.get_root(root)
        validate_relpath(relpath)
        digest=hashlib.sha256(data).hexdigest(); size=len(data) if size is None else int(size); mtime=0.0 if mtime is None else float(mtime)
        if embedding is None:
            from . import encoder
            embedding=encoder.encode_image(data)
            self.set_fingerprint(encoder.fingerprint(),{"model_id":config.MODEL_ID,"revision":config.MODEL_REVISION,"dimension":512,"dtype":"float32","normalize":"l2"})
        emb=np.asarray(embedding,dtype=np.float32).reshape(-1)
        if emb.size != 512 or not np.isfinite(emb).all(): raise ValueError("无效图片向量")
        n=float(np.linalg.norm(emb))
        if n < 1e-8: raise ValueError("零向量")
        emb=emb/n; blob=emb.tobytes(); now=utcnow()
        article_id=None
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                return self._add_image_tx(root, relpath, digest, size, mtime, blob, article, origin, shot_at, now)
            except Exception:
                self.conn.rollback()
                raise

    def _upsert_article_tx(self, article, now):
        """在既有事务内落一条文章记录，返回 article_id。

        article 只由本文的写入方构造（article_link / archive），字段缺失视为编程错误。
        """
        article_id=article.get("id")
        if not article_id: raise ValueError("article.id required")
        old=self.conn.execute("SELECT updated_at FROM articles WHERE article_id=?",(article_id,)).fetchone()
        updated=article.get("observed_at") or now
        if not old or updated > old[0]:
            self.conn.execute("INSERT INTO articles VALUES(?,?,?,?,?) ON CONFLICT(article_id) DO UPDATE SET title=excluded.title,source_url=excluded.source_url,published_at=excluded.published_at,updated_at=excluded.updated_at",(article_id,article.get("title") or "",article.get("url") or "",article.get("published_at"),updated))
        return article_id

    def _add_image_tx(self, root, relpath, digest, size, mtime, blob, article, origin, shot_at, now):
            article_id = self._upsert_article_tx(article, now) if article else None
            old=self.conn.execute("SELECT * FROM images WHERE root=? AND relpath=?",(root,relpath)).fetchone()
            if old and old["content_sha256"]==digest and old["state"]=="ready":
                self.conn.execute("UPDATE images SET size=?,mtime=?,article_id=COALESCE(?,article_id),shot_at=COALESCE(?,shot_at) WHERE id=?",(size,mtime,article_id,shot_at,old["id"]))
                status="unchanged"
            else:
                version=old["content_version"] if old and old["content_sha256"]==digest else str(uuid.uuid4())
                ingested=(old["ingested_at"] if old else None) or now
                self.conn.execute("INSERT INTO images(root,relpath,size,mtime,content_sha256,content_version,emb,article_id,shot_at,ingested_at,indexed_at,origin,state,last_error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'ready',NULL) ON CONFLICT(root,relpath) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,content_sha256=excluded.content_sha256,content_version=excluded.content_version,emb=excluded.emb,article_id=COALESCE(excluded.article_id,images.article_id),shot_at=COALESCE(excluded.shot_at,images.shot_at),ingested_at=COALESCE(images.ingested_at,excluded.ingested_at),indexed_at=excluded.indexed_at,origin=excluded.origin,state='ready',last_error=NULL",(root,relpath,size,mtime,digest,version,blob,article_id,shot_at,ingested,now,origin))
                status="new" if old is None else "updated"
            self.conn.execute("UPDATE index_meta SET generation=generation+1 WHERE id=1")
            self.conn.commit(); self.reload_snapshot()
            return {"status":status,"root":root,"relpath":relpath,"content_sha256":digest,"content_version":version if status!="unchanged" else old["content_version"]}

    def get(self, root: str, relpath: str):
        with self.lock: return self.conn.execute("SELECT * FROM images WHERE root=? AND relpath=?",(root,relpath)).fetchone()

    def failures(self, root=None, limit=100, offset=0):
        sql="SELECT root,relpath,size,mtime,last_error FROM images WHERE state='quarantined'"; args=[]
        if root: sql+=" AND root=?"; args.append(root)
        sql+=" ORDER BY root,relpath LIMIT ? OFFSET ?"; args.extend([limit,offset])
        with self.lock: return [dict(x) for x in self.conn.execute(sql,args)]

    def stats(self):
        with self.lock:
            roots=[dict(r) for r in self.conn.execute("SELECT root,COUNT(*) count,MAX(ingested_at) latest_ingested_at FROM images WHERE state='ready' GROUP BY root")]
            return {"generation":self._snapshot[0],"total":sum(r["count"] for r in roots),"roots":roots}

    # ---- 内容条目（文章 / 视频 / 图集 / 笔记）--------------------------------
    _CONTENT_FIELDS = ("root", "relpath", "platform", "kind", "title", "account", "url",
                       "published_at", "digest", "body", "cover_relpath", "media_count")

    @classmethod
    def _content_digest(cls, item):
        fields = {key: item.get(key) for key in cls._CONTENT_FIELDS}
        return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def sync_contents(self, items, *, prune_root=None, now=None):
        """把一次发现的内容条目落库，并重建受影响条目的全文索引与图片挂接。

        ``prune_root`` 给出时，该根下不在 ``items`` 里的旧条目会被删除——目录被删、
        视频被清理后索引不该继续留着幽灵条目。图片只解除引用，不删除图片记录。
        """
        now = now or utcnow()
        report = {"new": 0, "updated": 0, "unchanged": 0, "removed": 0}
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for item in items:
                    content_id = item["content_id"]
                    old = self.conn.execute(
                        "SELECT content_sha256 FROM contents WHERE content_id=?", (content_id,)
                    ).fetchone()
                    digest = self._content_digest(item)
                    if old and old[0] == digest:
                        self.conn.execute("UPDATE contents SET observed_at=? WHERE content_id=?",
                                          (item.get("observed_at"), content_id))
                        report["unchanged"] += 1
                    else:
                        self._upsert_content_tx(item, digest, now)
                        report["new" if old is None else "updated"] += 1
                    self._bind_images_tx(item)
                if prune_root:
                    keep = {item["content_id"] for item in items}
                    stale = self.conn.execute(
                        "SELECT content_id FROM contents WHERE root=?", (prune_root,)
                    ).fetchall()
                    for row in stale:
                        content_id = row["content_id"]
                        if content_id in keep:
                            continue
                        self.conn.execute("DELETE FROM content_fts WHERE content_id=?", (content_id,))
                        self.conn.execute("UPDATE images SET content_id=NULL WHERE content_id=?", (content_id,))
                        self.conn.execute("DELETE FROM contents WHERE content_id=?", (content_id,))
                        report["removed"] += 1
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return report

    def _upsert_content_tx(self, item, digest, now):
        from . import contents as content_index
        content_id = item["content_id"]
        self.conn.execute(
            "INSERT INTO contents(content_id,root,relpath,platform,kind,title,account,url,published_at,"
            "digest,body,cover_relpath,media_count,observed_at,content_sha256,work_key,indexed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(content_id) DO UPDATE SET "
            "root=excluded.root,relpath=excluded.relpath,platform=excluded.platform,kind=excluded.kind,"
            "title=excluded.title,account=excluded.account,url=excluded.url,published_at=excluded.published_at,"
            "digest=excluded.digest,body=excluded.body,cover_relpath=excluded.cover_relpath,"
            "media_count=excluded.media_count,observed_at=excluded.observed_at,"
            "content_sha256=excluded.content_sha256,work_key=excluded.work_key,indexed_at=excluded.indexed_at",
            (content_id, item["root"], item["relpath"], item["platform"], item["kind"], item["title"],
             item.get("account"), item.get("url"), item.get("published_at"), item.get("digest"),
             item.get("body"), item.get("cover_relpath"), int(item.get("media_count") or 0),
             item.get("observed_at"), digest, item.get("work_key"), now),
        )
        self.conn.execute("DELETE FROM content_fts WHERE content_id=?", (content_id,))
        self.conn.execute(
            "INSERT INTO content_fts(content_id,title,body,account) VALUES(?,?,?,?)",
            (content_id,
             " ".join(content_index.tokenize(item["title"])),
             " ".join(content_index.tokenize(item.get("body") or "")),
             " ".join(content_index.tokenize(item.get("account") or ""))),
        )

    def _bind_images_tx(self, item):
        """条目目录内的图片按路径前缀挂到条目上，详情页据此列出配图。"""
        if not item.get("is_dir", True):
            return
        prefix = item["relpath"] + "/"
        self.conn.execute(
            "UPDATE images SET content_id=? WHERE root=? AND substr(relpath,1,?)=?",
            (item["content_id"], item["root"], len(prefix), prefix),
        )

    def search_contents(self, query, *, platform=None, root=None, account=None, limit=24, offset=0):
        from . import contents as content_index
        tokens = content_index.tokenize(query)
        filters, filter_args = [], []
        if platform:
            filters.append("c.platform=?")
            filter_args.append(platform)
        if root:
            filters.append("c.root=?")
            filter_args.append(root)
        if account:
            filters.append("c.account=?")
            filter_args.append(account)
        join = "FROM content_fts JOIN contents c ON c.content_id=content_fts.content_id"
        # 先要求全部二元词同时命中（"手机壁纸"不会召回到只有"手"或"纸"的内容），
        # 没有结果才退回任意词命中，避免长查询一条都搜不到。
        attempts = []
        phrases = [token for token in tokens if len(token) >= 2]
        if phrases:
            attempts.append(" AND ".join(f'"{token}"' for token in phrases))
        if tokens:
            attempts.append(" OR ".join(f'"{token}"' for token in tokens))
        chosen, total, rows = None, 0, []
        with self.lock:
            for match in attempts:
                where = ["content_fts MATCH ?", *filters]
                args = [match, *filter_args]
                clause = "WHERE " + " AND ".join(where)
                count = self.conn.execute(f"SELECT COUNT(*) {join} {clause}", args).fetchone()[0]
                chosen = match
                total = int(count)
                if count:
                    break
            if chosen is None:
                clause = ("WHERE " + " AND ".join(filters)) if filters else ""
                total = int(self.conn.execute(
                    f"SELECT COUNT(*) FROM contents c {clause}", filter_args).fetchone()[0])
                rows = self.conn.execute(
                    f"SELECT c.*, 0 AS score FROM contents c {clause} "
                    "ORDER BY COALESCE(c.published_at,c.indexed_at) DESC LIMIT ? OFFSET ?",
                    [*filter_args, int(limit), int(offset)],
                ).fetchall()
            else:
                clause = "WHERE " + " AND ".join(["content_fts MATCH ?", *filters])
                args = [chosen, *filter_args]
                rows = self.conn.execute(
                    f"SELECT c.*, bm25(content_fts, 4.0, 1.0, 2.0) AS score {join} {clause} "
                    "ORDER BY score LIMIT ? OFFSET ?",
                    [*args, int(limit), int(offset)],
                ).fetchall()
        return [dict(row) for row in rows], total, tokens

    def content_detail(self, content_id):
        with self.lock:
            row = self.conn.execute("SELECT * FROM contents WHERE content_id=?", (content_id,)).fetchone()
            if not row:
                return None
            images = [dict(r) for r in self.conn.execute(
                "SELECT root,relpath,content_version,size,shot_at FROM images "
                "WHERE content_id=? AND state='ready' ORDER BY relpath", (content_id,))]
        return {**dict(row), "images": images}

    def content_facets(self):
        with self.lock:
            platforms = [dict(r) for r in self.conn.execute(
                "SELECT platform,COUNT(*) count FROM contents GROUP BY platform ORDER BY count DESC")]
            accounts = [dict(r) for r in self.conn.execute(
                "SELECT platform,account,COUNT(*) count FROM contents "
                "WHERE account IS NOT NULL AND account<>'' GROUP BY platform,account "
                "ORDER BY count DESC LIMIT 200")]
            total = self.conn.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
        return {"platforms": platforms, "accounts": accounts, "total": int(total)}

    # ---- 标签（文件 / 内容条目）--------------------------------------------
    def list_tags(self):
        """标签字典及使用次数（文件标签按文件行计，作品标签按条目行计）。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT tag,count FROM ("
                " SELECT t.name AS tag,"
                " (SELECT COUNT(*) FROM image_tags i WHERE i.tag=t.name)"
                " + (SELECT COUNT(*) FROM content_tags c WHERE c.tag=t.name) AS count"
                " FROM tags t) WHERE count>0 ORDER BY tag"
            ).fetchall()
        return [{"tag": row["tag"], "count": int(row["count"])} for row in rows]

    def _ensure_tag_tx(self, tag):
        self.conn.execute("INSERT OR IGNORE INTO tags(name,created_at) VALUES(?,?)", (tag, utcnow()))

    def image_tags(self, root, relpath):
        """文件当前内容可见的标签；同 content_sha256 的归档副本共享同一套标签。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT t.tag FROM image_tags t JOIN images i ON i.content_sha256=t.content_sha256"
                " WHERE i.root=? AND i.relpath=?", (root, relpath)).fetchall()
        return [row["tag"] for row in rows]

    def tag_image(self, root, relpath, tag, mode="add"):
        """给文件打/去标签；同内容版本的所有副本一起生效。"""
        row = self.get(root, relpath)
        if row is None:
            raise KeyError("图片未索引")
        sha = row["content_sha256"]
        if not sha:
            raise ValueError("图片缺少内容摘要")
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if mode == "add":
                    self._ensure_tag_tx(tag)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO image_tags(root,relpath,tag,content_sha256,created_at)"
                        " SELECT root,relpath,?,?,? FROM images WHERE content_sha256=?",
                        (tag, sha, utcnow(), sha))
                elif mode == "remove":
                    self.conn.execute("DELETE FROM image_tags WHERE tag=? AND content_sha256=?", (tag, sha))
                else:
                    raise ValueError("mode 必须是 add 或 remove")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return self.image_tags(root, relpath)

    # ---- NSFW 初筛结果 ------------------------------------------------------
    def set_nsfw(self, root, relpath, content_sha256, *, score, label, status="ok",
                 kind="image", ts_seconds=0.0, stage="first", model_version=None,
                 nsfw_type=None):
        """写入一条 NSFW 判定。

        唯一键含 content_sha256：图片换内容就等于换一条记录，旧判定自然失效，不会被
        当成新内容的结论。ts_seconds 对图片固定 0.0（SQLite 里 NULL 不参与唯一比较，
        留空会重复插入）。人工复核字段（reviewed_by/reviewed_at）不在 UPDATE 列表里，
        机器重跑初筛不会抹掉人工结论。
        """
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute(
                    "INSERT INTO nsfw_results(root,relpath,content_sha256,kind,ts_seconds,stage,"
                    "nsfw_type,score,label,status,model_version,created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(root,relpath,content_sha256,kind,stage,ts_seconds) DO UPDATE SET"
                    " nsfw_type=excluded.nsfw_type,score=excluded.score,label=excluded.label,"
                    " status=excluded.status,model_version=excluded.model_version,"
                    " created_at=excluded.created_at",
                    (root, relpath, content_sha256, kind, float(ts_seconds or 0.0), stage,
                     nsfw_type, None if score is None else float(score), label, status,
                     model_version, utcnow()))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return {"score": score, "label": label, "status": status, "stage": stage}

    def nsfw_summary(self, root, relpath):
        """文件当前内容的初筛结论；内容版本已变或从未跑过时返回 None。"""
        with self.lock:
            row = self.conn.execute(
                "SELECT n.score,n.label,n.status,n.stage,n.nsfw_type,n.reviewed_by FROM nsfw_results n"
                " JOIN images i ON i.root=n.root AND i.relpath=n.relpath"
                " AND i.content_sha256=n.content_sha256"
                " WHERE n.root=? AND n.relpath=? AND n.kind='image' AND n.stage='first'"
                " ORDER BY n.id DESC LIMIT 1", (root, relpath)).fetchone()
        if row is None:
            return None
        return {"score": row["score"], "label": row["label"], "status": row["status"],
                "stage": row["stage"], "nsfw_type": row["nsfw_type"],
                "reviewed_by": row["reviewed_by"]}

    def content_nsfw_flags(self, content_ids):
        """内容条目下图片的初筛命中统计：{content_id: {"total": n, "flagged": n}}。

        条目本身没有「是否 NSFW」这个属性——高风险是图片级的判定，内容级只做聚合：
        该条目已入库的图片里有多少张命中初筛，前端据此在卡片上挂角标并支持筛选。
        摘要必须与图片行一致，否则那是旧内容版本的结论，不能算到当前条目头上。
        """
        ids = [cid for cid in content_ids if cid]
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self.lock:
            rows = self.conn.execute(
                # 左连接：没跑过初筛的图片也要计入 total，否则角标分母会漏算，
                # 而摘要不一致的旧结论只让 flagged 归零、不影响这张图本身的存在。
                "SELECT i.content_id AS cid, COUNT(*) AS total,"
                " SUM(CASE WHEN n.label='nsfw' AND n.status='ok' THEN 1 ELSE 0 END) AS flagged"
                " FROM images i LEFT JOIN nsfw_results n"
                " ON n.root=i.root AND n.relpath=i.relpath AND n.content_sha256=i.content_sha256"
                " AND n.kind='image' AND n.stage='first' AND n.ts_seconds=0.0"
                f" WHERE i.state='ready' AND i.content_id IN ({marks}) GROUP BY i.content_id",
                ids).fetchall()
        return {row["cid"]: {"total": int(row["total"]), "flagged": int(row["flagged"] or 0)}
                for row in rows}

    # ---- 视频代表帧 -------------------------------------------------------
    def video_record(self, root, relpath):
        """某个视频已入库的抽帧记录；扫描时用它的内容摘要判断是否可跳过。"""
        with self.lock:
            row = self.conn.execute(
                "SELECT content_sha256,duration,frame_count,sampled_at FROM videos"
                " WHERE root=? AND video_relpath=? ORDER BY sampled_at DESC LIMIT 1",
                (root, relpath)).fetchone()
        return dict(row) if row else None

    def replace_frames(self, root, relpath, content_sha256, duration, frames, sampled_at=None):
        """用一次抽帧结果替换该视频的旧帧，返回被替换掉的旧帧 [(ts, frame_sha256)]。

        删旧写新在同一个事务里完成：中途失败时既不会留下半套帧，也不会出现
        「库里有帧记录但 JPEG 已被覆盖」的组合。
        """
        now = utcnow()
        with self.lock:
            old = [(float(row["ts_seconds"]), row["frame_sha256"]) for row in self.conn.execute(
                "SELECT ts_seconds,frame_sha256 FROM video_frames WHERE root=? AND video_relpath=?",
                (root, relpath)).fetchall()]
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.execute("DELETE FROM video_frames WHERE root=? AND video_relpath=?", (root, relpath))
                self.conn.execute("DELETE FROM videos WHERE root=? AND video_relpath=?", (root, relpath))
                self.conn.execute(
                    "INSERT INTO videos(root,video_relpath,content_sha256,duration,frame_count,sampled_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (root, relpath, content_sha256, duration, len(frames), sampled_at or now))
                self.conn.executemany(
                    "INSERT INTO video_frames(root,video_relpath,content_sha256,ts_seconds,frame_sha256,emb,created_at)"
                    " VALUES(?,?,?,?,?,?,?)",
                    [(root, relpath, content_sha256, float(ts), sha, emb, now) for ts, sha, emb in frames])
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        self.reload_frame_snapshot()
        return old

    def drop_frames(self, root, relpath=None):
        """删除某个视频（或整个媒体根）的帧记录，返回 [(root, relpath, ts, frame_sha256)]。"""
        sql = "SELECT root,video_relpath,ts_seconds,frame_sha256 FROM video_frames WHERE root=?"
        params = [root]
        if relpath is not None:
            sql += " AND video_relpath=?"
            params.append(relpath)
        with self.lock:
            rows = [dict(row) for row in self.conn.execute(sql, tuple(params)).fetchall()]
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if relpath is None:
                    self.conn.execute("DELETE FROM video_frames WHERE root=?", (root,))
                    self.conn.execute("DELETE FROM videos WHERE root=?", (root,))
                else:
                    self.conn.execute("DELETE FROM video_frames WHERE root=? AND video_relpath=?", (root, relpath))
                    self.conn.execute("DELETE FROM videos WHERE root=? AND video_relpath=?", (root, relpath))
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        self.reload_frame_snapshot()
        return [(row["root"], row["video_relpath"], float(row["ts_seconds"]), row["frame_sha256"]) for row in rows]

    def reload_frame_snapshot(self):
        """把帧向量装进只读矩阵，供视频检索复用图片检索的排序口径。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT f.id,f.root,f.video_relpath,f.content_sha256,f.ts_seconds,f.frame_sha256,f.emb,"
                " v.duration,v.frame_count,v.sampled_at"
                " FROM video_frames f JOIN videos v"
                " ON v.root=f.root AND v.video_relpath=f.video_relpath AND v.content_sha256=f.content_sha256"
                " WHERE f.emb IS NOT NULL ORDER BY f.root,f.video_relpath,f.ts_seconds").fetchall()
            records = []
            vectors = []
            for row in rows:
                vec = np.frombuffer(row["emb"], dtype=np.float32)
                if vec.size == 512 and np.isfinite(vec).all():
                    records.append(dict(row))
                    vectors.append(vec.copy())
            matrix = np.vstack(vectors).astype(np.float32) if vectors else np.empty((0, 512), dtype=np.float32)
            matrix.setflags(write=False)
            self._frame_snapshot = (records, matrix)

    def frame_snapshot(self):
        return self._frame_snapshot

    def images_by_tag(self, tag, limit=100, offset=0):
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT i.root,i.relpath,i.content_version,i.size,i.shot_at FROM images i"
                " JOIN image_tags t ON t.content_sha256=i.content_sha256"
                " WHERE t.tag=? AND i.state='ready' ORDER BY i.root,i.relpath LIMIT ? OFFSET ?",
                (tag, int(limit), int(offset))).fetchall()
        return [dict(row) for row in rows]

    def content_tags(self, content_id):
        """条目当前内容可见的标签；同 work_key（平台+稳定作品 ID）的归档副本共享。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT DISTINCT t.tag FROM content_tags t JOIN contents c ON c.content_id=t.content_id"
                " WHERE COALESCE(c.work_key,c.content_id)="
                " (SELECT COALESCE(work_key,content_id) FROM contents WHERE content_id=?)",
                (content_id,)).fetchall()
        return [row["tag"] for row in rows]

    def tag_content(self, content_id, tag, mode="add"):
        """给作品打/去标签；同 work_key 的所有归档副本一起生效。"""
        with self.lock:
            row = self.conn.execute(
                "SELECT COALESCE(work_key,content_id) FROM contents WHERE content_id=?",
                (content_id,)).fetchone()
        if row is None:
            raise KeyError("内容条目不存在")
        work_key = row[0]
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if mode == "add":
                    self._ensure_tag_tx(tag)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO content_tags(content_id,tag,created_at)"
                        " SELECT content_id,?,? FROM contents"
                        " WHERE COALESCE(work_key,content_id)=(SELECT COALESCE(work_key,content_id)"
                        " FROM contents WHERE content_id=?)",
                        (tag, utcnow(), content_id))
                elif mode == "remove":
                    self.conn.execute(
                        "DELETE FROM content_tags WHERE tag=? AND content_id IN"
                        " (SELECT content_id FROM contents WHERE COALESCE(work_key,content_id)=?)",
                        (tag, work_key))
                else:
                    raise ValueError("mode 必须是 add 或 remove")
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return self.content_tags(content_id)

    def contents_by_tag(self, tag, limit=100, offset=0):
        """按标签列出作品；同一 work_key 的归档副本只保留一条。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT c.* FROM contents c JOIN content_tags t ON t.content_id=c.content_id"
                " WHERE t.tag=? GROUP BY COALESCE(c.work_key,c.content_id)"
                " ORDER BY COALESCE(MAX(c.published_at),MAX(c.indexed_at)) DESC LIMIT ? OFFSET ?",
                (tag, int(limit), int(offset))).fetchall()
        return [dict(row) for row in rows]

    # ---- 独立文本（TXT/MD/HTML 全文）--------------------------------------
    def sync_standalone_texts(self, items, *, prune_root=None, now=None):
        """把一次发现的独立文本落库并重建全文索引。

        ``items`` 元素含 ``root/relpath/content_sha256/kind/title/body``；内容摘要
        未变的条目只刷新 ``indexed_at``，避免每次扫描都重建 FTS。``prune_root``
        给出时，该根下本轮未出现的旧条目删除——文件被删或移走后不留幽灵。
        """
        now = now or utcnow()
        report = {"new": 0, "updated": 0, "unchanged": 0, "removed": 0}
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for item in items:
                    root, relpath = item["root"], item["relpath"]
                    sha = item.get("content_sha256")
                    old = self.conn.execute(
                        "SELECT content_sha256 FROM standalone_texts WHERE root=? AND relpath=?",
                        (root, relpath),
                    ).fetchone()
                    if old and old[0] == sha:
                        self.conn.execute(
                            "UPDATE standalone_texts SET indexed_at=? WHERE root=? AND relpath=?",
                            (now, root, relpath),
                        )
                        report["unchanged"] += 1
                    else:
                        self._upsert_standalone_text_tx(item, sha, now)
                        report["new" if old is None else "updated"] += 1
                if prune_root:
                    keep = {(item["root"], item["relpath"]) for item in items}
                    stale = self.conn.execute(
                        "SELECT id,root,relpath FROM standalone_texts WHERE root=?", (prune_root,)
                    ).fetchall()
                    for row in stale:
                        if (row["root"], row["relpath"]) in keep:
                            continue
                        self.conn.execute("DELETE FROM text_fts WHERE text_id=?", (row["id"],))
                        self.conn.execute("DELETE FROM standalone_texts WHERE id=?", (row["id"],))
                        report["removed"] += 1
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return report

    def _upsert_standalone_text_tx(self, item, sha, now):
        from . import contents as content_index
        row = self.conn.execute(
            "SELECT id FROM standalone_texts WHERE root=? AND relpath=?",
            (item["root"], item["relpath"]),
        ).fetchone()
        body = item.get("body") or ""
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO standalone_texts(root,relpath,content_sha256,kind,title,body,indexed_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (item["root"], item["relpath"], sha, item.get("kind"), item.get("title"), body, now),
            )
            text_id = cur.lastrowid
        else:
            text_id = row["id"]
            self.conn.execute(
                "UPDATE standalone_texts SET content_sha256=?,kind=?,title=?,body=?,indexed_at=?"
                " WHERE id=?",
                (sha, item.get("kind"), item.get("title"), body, now, text_id),
            )
        self.conn.execute("DELETE FROM text_fts WHERE text_id=?", (text_id,))
        self.conn.execute(
            "INSERT INTO text_fts(text_id,title,body) VALUES(?,?,?)",
            (text_id,
             " ".join(content_index.tokenize(item.get("title") or "")),
             " ".join(content_index.tokenize(body))),
        )

    def search_texts(self, query, *, root=None, kind=None, limit=24, offset=0):
        """独立文本全文检索；返回 (rows, total, tokens)。"""
        from . import contents as content_index
        tokens = content_index.tokenize(query)
        filters, filter_args = [], []
        if root:
            filters.append("t.root=?")
            filter_args.append(root)
        if kind:
            filters.append("t.kind=?")
            filter_args.append(kind)
        join = "FROM text_fts JOIN standalone_texts t ON t.id=text_fts.text_id"
        attempts = []
        phrases = [token for token in tokens if len(token) >= 2]
        if phrases:
            attempts.append(" AND ".join(f'"{token}"' for token in phrases))
        if tokens:
            attempts.append(" OR ".join(f'"{token}"' for token in tokens))
        chosen, total, rows = None, 0, []
        with self.lock:
            for match in attempts:
                clause = "WHERE " + " AND ".join(["text_fts MATCH ?", *filters])
                args = [match, *filter_args]
                count = self.conn.execute(f"SELECT COUNT(*) {join} {clause}", args).fetchone()[0]
                chosen, total = match, int(count)
                if count:
                    break
            if chosen is None:
                clause = ("WHERE " + " AND ".join(filters)) if filters else ""
                total = int(self.conn.execute(
                    f"SELECT COUNT(*) FROM standalone_texts t {clause}", filter_args).fetchone()[0])
                rows = self.conn.execute(
                    f"SELECT t.*, 0 AS score FROM standalone_texts t {clause} "
                    "ORDER BY t.indexed_at DESC LIMIT ? OFFSET ?",
                    [*filter_args, int(limit), int(offset)],
                ).fetchall()
            else:
                clause = "WHERE " + " AND ".join(["text_fts MATCH ?", *filters])
                rows = self.conn.execute(
                    f"SELECT t.*, bm25(text_fts, 0.0, 1.0, 3.0) AS score {join} {clause} "
                    "ORDER BY score LIMIT ? OFFSET ?",
                    [chosen, *filter_args, int(limit), int(offset)],
                ).fetchall()
        return [dict(row) for row in rows], total, tokens

    def text_detail(self, text_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM standalone_texts WHERE id=?", (int(text_id),)
            ).fetchone()
        return dict(row) if row else None

    def texts_facets(self):
        with self.lock:
            kinds = [dict(r) for r in self.conn.execute(
                "SELECT kind,COUNT(*) count FROM standalone_texts GROUP BY kind ORDER BY count DESC")]
            roots = [dict(r) for r in self.conn.execute(
                "SELECT root,COUNT(*) count FROM standalone_texts GROUP BY root ORDER BY count DESC")]
            total = int(self.conn.execute("SELECT COUNT(*) FROM standalone_texts").fetchone()[0])
        return {"kinds": kinds, "roots": roots, "total": total}

    # ---- 独立文本（TXT / MD / HTML 全文）---------------------------------
    def sync_standalone_texts(self, items, *, prune_root=None, now=None):
        """把一次发现的独立文本落库；内容摘要未变就不重建全文索引。

        ``prune_root`` 给出时，该根下不在 ``items`` 里的旧文本会被删除——文件被清理
        后索引不该继续留着幽灵条目。文本不参与图片向量检索，删掉只影响文本检索。
        """
        now = now or utcnow()
        report = {"new": 0, "updated": 0, "unchanged": 0, "removed": 0}
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for item in items:
                    root, relpath = item["root"], item["relpath"]
                    sha = item.get("content_sha256")
                    old = self.conn.execute(
                        "SELECT content_sha256 FROM standalone_texts WHERE root=? AND relpath=?",
                        (root, relpath),
                    ).fetchone()
                    if old and old[0] == sha:
                        self.conn.execute(
                            "UPDATE standalone_texts SET indexed_at=? WHERE root=? AND relpath=?",
                            (now, root, relpath),
                        )
                        report["unchanged"] += 1
                    else:
                        self._upsert_standalone_text_tx(item, sha, now)
                        report["new" if old is None else "updated"] += 1
                if prune_root:
                    keep = {(item["root"], item["relpath"]) for item in items}
                    stale = self.conn.execute(
                        "SELECT id,root,relpath FROM standalone_texts WHERE root=?", (prune_root,)
                    ).fetchall()
                    for row in stale:
                        if (row["root"], row["relpath"]) in keep:
                            continue
                        self.conn.execute("DELETE FROM text_fts WHERE text_id=?", (row["id"],))
                        self.conn.execute("DELETE FROM standalone_texts WHERE id=?", (row["id"],))
                        report["removed"] += 1
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return report

    def _upsert_standalone_text_tx(self, item, sha, now):
        from . import contents as content_index
        row = self.conn.execute(
            "SELECT id FROM standalone_texts WHERE root=? AND relpath=?",
            (item["root"], item["relpath"]),
        ).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO standalone_texts(root,relpath,content_sha256,kind,title,body,indexed_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (item["root"], item["relpath"], sha, item.get("kind"),
                 item.get("title"), item.get("body"), now),
            )
            text_id = cur.lastrowid
        else:
            text_id = row["id"]
            self.conn.execute(
                "UPDATE standalone_texts SET content_sha256=?,kind=?,title=?,body=?,indexed_at=?"
                " WHERE id=?",
                (sha, item.get("kind"), item.get("title"), item.get("body"), now, text_id),
            )
        # 与 content_fts 同法：FTS 表自带一份切词结果，删除时按 text_id 整体清掉重建，
        # 避免 FTS5 'delete' 指令需要回填旧值。
        self.conn.execute("DELETE FROM text_fts WHERE text_id=?", (text_id,))
        self.conn.execute(
            "INSERT INTO text_fts(text_id,title,body) VALUES(?,?,?)",
            (text_id,
             " ".join(content_index.tokenize(item.get("title") or "")),
             " ".join(content_index.tokenize(item.get("body") or ""))),
        )

    def search_texts(self, query, *, root=None, kind=None, limit=24, offset=0):
        from . import contents as content_index
        tokens = content_index.tokenize(query)
        filters, filter_args = [], []
        if root:
            filters.append("t.root=?")
            filter_args.append(root)
        if kind:
            filters.append("t.kind=?")
            filter_args.append(kind)
        join = "FROM text_fts JOIN standalone_texts t ON t.id=text_fts.text_id"
        # 先要求全部二元词同时命中，没有结果才退回任意词命中（与 search_contents 同口径）。
        attempts = []
        phrases = [token for token in tokens if len(token) >= 2]
        if phrases:
            attempts.append(" AND ".join(f'"{token}"' for token in phrases))
        if tokens:
            attempts.append(" OR ".join(f'"{token}"' for token in tokens))
        chosen, total, rows = None, 0, []
        with self.lock:
            for match in attempts:
                clause = "WHERE " + " AND ".join(["text_fts MATCH ?", *filters])
                args = [match, *filter_args]
                count = self.conn.execute(f"SELECT COUNT(*) {join} {clause}", args).fetchone()[0]
                chosen, total = match, int(count)
                if count:
                    break
            if chosen is None:
                clause = ("WHERE " + " AND ".join(filters)) if filters else ""
                total = int(self.conn.execute(
                    f"SELECT COUNT(*) FROM standalone_texts t {clause}", filter_args).fetchone()[0])
                rows = self.conn.execute(
                    f"SELECT t.*, 0 AS score FROM standalone_texts t {clause} "
                    "ORDER BY t.indexed_at DESC LIMIT ? OFFSET ?",
                    [*filter_args, int(limit), int(offset)],
                ).fetchall()
            else:
                clause = "WHERE " + " AND ".join(["text_fts MATCH ?", *filters])
                rows = self.conn.execute(
                    f"SELECT t.*, bm25(text_fts, 0.0, 1.0, 3.0) AS score {join} {clause} "
                    "ORDER BY score LIMIT ? OFFSET ?",
                    [*[match, *filter_args], int(limit), int(offset)],
                ).fetchall()
        return [dict(row) for row in rows], total, tokens

    def text_detail(self, text_id):
        with self.lock:
            row = self.conn.execute("SELECT * FROM standalone_texts WHERE id=?", (int(text_id),)).fetchone()
        return dict(row) if row else None

    def texts_facets(self):
        with self.lock:
            kinds = [dict(r) for r in self.conn.execute(
                "SELECT kind,COUNT(*) count FROM standalone_texts GROUP BY kind ORDER BY count DESC")]
            roots = [dict(r) for r in self.conn.execute(
                "SELECT root,COUNT(*) count FROM standalone_texts GROUP BY root ORDER BY count DESC")]
            total = self.conn.execute("SELECT COUNT(*) FROM standalone_texts").fetchone()[0]
        return {"kinds": kinds, "roots": roots, "total": int(total)}

STORE=Store()

def import_legacy(source_db: Path | str, root_mapping: dict[str,str], *, store: Store = STORE):
    """只读导入旧图片索引。root_mapping 为旧绝对根目录到显式新 root ID 的映射。

    旧向量来源不可充分证明时一律从目标媒体重新编码；单事务插入，已有键不覆盖。
    """
    import os, hashlib
    from . import media, encoder
    source=Path(source_db).resolve()
    src=sqlite3.connect(f"file:{source.as_posix()}?mode=ro",uri=True)
    src.row_factory=sqlite3.Row
    try:
        tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table="images" if "images" in tables else None
        if not table: raise ValueError("旧库缺少 images 表")
        columns={r[1] for r in src.execute(f"PRAGMA table_info({table})")}
        required={"path","mtime","size"}
        if not required.issubset(columns): raise ValueError("旧库缺少 path/mtime/size 字段")
        select=["path","mtime","size"]+[c for c in ("shot_at",) if c in columns]
        rows=src.execute(f"SELECT {','.join(select)} FROM {table}").fetchall()
        mappings=sorted(((Path(old).resolve(),target) for old,target in root_mapping.items()),key=lambda x:len(str(x[0])),reverse=True)
        report={"inserted":0,"existing":0,"skipped":0,"invalid":0}
        prepared=[]
        for row in rows:
            try:
                raw=Path(row["path"])
                if not raw.is_absolute():
                    report["invalid"]+=1
                    continue
                # 先按路径段做纯词法筛选，避免对数万条未映射的挂载盘文件执行 realpath。
                possible=next(((base,target) for base,target in mappings if raw.is_relative_to(base)),None)
                if not possible:
                    report["skipped"]+=1
                    continue
                resolved=raw.resolve()
                base,target_root=possible
                if not resolved.is_relative_to(base):
                    report["invalid"]+=1
                    continue
                relpath=resolved.relative_to(base).as_posix()
                media.validate_relpath(relpath)
                config.get_root(target_root)
                with store.lock:
                    if store.conn.execute("SELECT 1 FROM images WHERE root=? AND relpath=?",(target_root,relpath)).fetchone(): report["existing"]+=1; continue
                data=media.read_bytes(target_root,relpath)
                if len(data)!=int(row["size"]):
                    # 老库的字节大小可能来自历史版本；以当前目标为准，不采用旧向量。
                    pass
                embedding=encoder.encode_image(data)
                vec=np.asarray(embedding,dtype=np.float32).reshape(-1)
                if vec.size!=512 or not np.isfinite(vec).all() or np.linalg.norm(vec)<1e-8: raise ValueError("invalid embedding")
                vec/=np.linalg.norm(vec)
                prepared.append((target_root,relpath,len(data),float(row["mtime"] or 0),hashlib.sha256(data).hexdigest(),str(uuid.uuid4()),vec.tobytes(),row["shot_at"] if "shot_at" in columns else None))
            except (OSError,ValueError,KeyError): report["invalid"]+=1
        now=utcnow()
        with store.lock:
            store.conn.execute("BEGIN IMMEDIATE")
            try:
                for values in prepared:
                    root,relpath,size,mtime,digest,version,emb,shot=values
                    cur=store.conn.execute("INSERT OR IGNORE INTO images(root,relpath,size,mtime,content_sha256,content_version,emb,shot_at,ingested_at,indexed_at,origin,state) VALUES(?,?,?,?,?,?,?,?,?,?,'legacy_import','ready')",(root,relpath,size,mtime,digest,version,emb,shot,now,now))
                    if cur.rowcount: report["inserted"]+=1
                    else: report["existing"]+=1
                if report["inserted"]: store.conn.execute("UPDATE index_meta SET generation=generation+1 WHERE id=1")
                store.conn.commit(); store.reload_snapshot()
            except Exception:
                store.conn.rollback(); raise
        return report
    finally: src.close()
