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
CREATE TABLE IF NOT EXISTS contents(content_id TEXT PRIMARY KEY,root TEXT NOT NULL,relpath TEXT NOT NULL,platform TEXT NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,account TEXT,url TEXT,published_at TEXT,digest TEXT,body TEXT,cover_relpath TEXT,media_count INTEGER NOT NULL DEFAULT 0,observed_at TEXT,content_sha256 TEXT NOT NULL,indexed_at TEXT NOT NULL,UNIQUE(root,relpath));
CREATE INDEX IF NOT EXISTS idx_contents_root ON contents(root);
CREATE INDEX IF NOT EXISTS idx_contents_platform ON contents(platform);
CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(content_id UNINDEXED,title,body,account,tokenize='unicode61');
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
        self.conn.execute("PRAGMA user_version=2")
        self.conn.commit()
        from .encoder import fingerprint
        self.set_fingerprint(fingerprint(), {"model_id": config.MODEL_ID, "revision": config.MODEL_REVISION, "dimension": 512, "dtype": "float32", "normalize": "l2"})
        self._snapshot = (0, [], np.empty((0, 512), dtype=np.float32))
        self.reload_snapshot()

    def _migrate(self):
        """把旧库补齐到当前结构；已有列一律不动，避免覆盖线上数据。"""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(images)")}
        if "content_id" not in columns:
            self.conn.execute("ALTER TABLE images ADD COLUMN content_id TEXT")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_images_content ON images(content_id)")

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
            "digest,body,cover_relpath,media_count,observed_at,content_sha256,indexed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(content_id) DO UPDATE SET "
            "root=excluded.root,relpath=excluded.relpath,platform=excluded.platform,kind=excluded.kind,"
            "title=excluded.title,account=excluded.account,url=excluded.url,published_at=excluded.published_at,"
            "digest=excluded.digest,body=excluded.body,cover_relpath=excluded.cover_relpath,"
            "media_count=excluded.media_count,observed_at=excluded.observed_at,"
            "content_sha256=excluded.content_sha256,indexed_at=excluded.indexed_at",
            (content_id, item["root"], item["relpath"], item["platform"], item["kind"], item["title"],
             item.get("account"), item.get("url"), item.get("published_at"), item.get("digest"),
             item.get("body"), item.get("cover_relpath"), int(item.get("media_count") or 0),
             item.get("observed_at"), digest, now),
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
