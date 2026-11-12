"""重建候选发布、取消与历史身份约束。"""
import sqlite3
from io import BytesIO
import numpy as np
from PIL import Image
import pytest

from app import config, media, scan, encoder
from app.store import Store, import_legacy

def _png(color):
    out=BytesIO(); Image.new("RGB",(8,8),color).save(out,"PNG"); return out.getvalue()

def _setup(monkeypatch,tmp_path):
    root=tmp_path/"media"; root.mkdir()
    monkeypatch.setattr(config,"MEDIA_ROOTS",({"id":"r","type":"local","path":str(root),"writable":True},))
    store=Store(tmp_path/"index.sqlite")
    monkeypatch.setattr(scan,"STORE",store)
    monkeypatch.setattr(media,"read_bytes",lambda root_id,path: content[path])
    monkeypatch.setattr(media,"stat_entry",lambda root_id,path: media.MediaEntry(root_id,path,len(content[path]),2))
    monkeypatch.setattr(encoder,"encode_image",lambda data: np.eye(1,512,0,dtype=np.float32)[0])
    return store

def test_rebuild_cancel_does_not_publish_candidate(monkeypatch,tmp_path):
    global content
    content={"a.png":_png("red")}
    store=_setup(monkeypatch,tmp_path)
    store.add_image("r","a.png",content["a.png"],embedding=np.eye(1,512,0,dtype=np.float32)[0],size=len(content["a.png"]),mtime=1)
    before=store.get("r","a.png")["content_version"]
    replacement=_png("blue"); content["a.png"]=replacement
    monkeypatch.setattr(media,"list_entries",lambda root:([media.MediaEntry(root,"a.png",len(replacement),2)],True))
    calls={"n":0}
    def cancel_after_candidate():
        calls["n"]+=1
        return calls["n"]>=4
    result=scan.scan_root("r",rebuild=True,cancel=cancel_after_candidate)
    assert result["status"]=="cancelled"
    row=store.get("r","a.png")
    assert row["content_version"]==before and row["size"]!=len(replacement)

def test_successful_rebuild_preserves_ingested_and_deleted_tombstone(monkeypatch,tmp_path):
    global content
    content={"a.png":_png("red")}
    store=_setup(monkeypatch,tmp_path)
    store.add_image("r","a.png",content["a.png"],embedding=np.eye(1,512,0,dtype=np.float32)[0],size=len(content["a.png"]),mtime=1,origin="legacy_import")
    store.add_image("r","gone.png",_png("green"),embedding=np.eye(1,512,0,dtype=np.float32)[0],origin="legacy_import")
    original=store.get("r","a.png"); tombstone=store.get("r","gone.png")
    changed=_png("blue"); content["a.png"]=changed
    monkeypatch.setattr(media,"list_entries",lambda root:([media.MediaEntry(root,"a.png",len(changed),2)],True))
    result=scan.scan_root("r",rebuild=True)
    assert result["complete"] is True
    current=store.get("r","a.png"); gone=store.get("r","gone.png")
    assert current["content_version"]!=original["content_version"]
    assert current["ingested_at"]==original["ingested_at"]
    assert current["origin"]=="legacy_import"
    assert gone["state"]=="deleted" and gone["ingested_at"]==tombstone["ingested_at"]

def test_rebuild_model_failure_keeps_formal_snapshot(monkeypatch,tmp_path):
    global content
    content={"a.png":_png("red")}
    store=_setup(monkeypatch,tmp_path)
    store.add_image("r","a.png",content["a.png"],embedding=np.eye(1,512,0,dtype=np.float32)[0])
    original=store.get("r","a.png")["content_version"]
    content["a.png"]=_png("blue")
    monkeypatch.setattr(media,"list_entries",lambda root:([media.MediaEntry(root,"a.png",len(content["a.png"]),2)],True))
    monkeypatch.setattr(encoder,"encode_image",lambda data: (_ for _ in ()).throw(RuntimeError("model failure")))
    result=scan.scan_root("r",rebuild=True)
    assert result["status"]=="partial" and not result["complete"]
    assert store.get("r","a.png")["content_version"]==original

def test_incomplete_listing_does_not_delete(monkeypatch,tmp_path):
    global content
    content={}
    store=_setup(monkeypatch,tmp_path)
    store.add_image("r","a.png",b"old",embedding=np.eye(1,512,0,dtype=np.float32)[0])
    monkeypatch.setattr(media,"list_entries",lambda root:([],False))
    with pytest.raises(OSError): scan.scan_root("r",rebuild=True)
    assert store.get("r","a.png")["state"]=="ready"

def test_legacy_import_is_idempotent_and_reencodes(monkeypatch,tmp_path):
    global content
    data=_png("yellow"); content={"a.png":data}
    store=_setup(monkeypatch,tmp_path)
    source=tmp_path/"old.sqlite"
    db=sqlite3.connect(source)
    db.execute("CREATE TABLE images(path TEXT,mtime REAL,size INTEGER,shot_at TEXT,emb BLOB)")
    db.execute("INSERT INTO images VALUES(?,?,?,?,?)",("/old/root/a.png",10,len(data),None,b"invalid-old-vector")); db.commit(); db.close()
    monkeypatch.setattr(media,"read_bytes",lambda root_id,path:content[path])
    one=import_legacy(source,{"/old/root":"r"},store=store)
    ingested=store.get("r","a.png")["ingested_at"]
    two=import_legacy(source,{"/old/root":"r"},store=store)
    assert one["inserted"]==1 and two["existing"]==1 and two["inserted"]==0
    assert store.get("r","a.png")["origin"]=="legacy_import"
    assert store.get("r","a.png")["ingested_at"]==ingested


def test_source_change_between_list_and_read_keeps_formal_row(monkeypatch, tmp_path):
    global content
    content = {"a.png": _png("red")}
    store = _setup(monkeypatch, tmp_path)
    old = store.add_image("r", "a.png", content["a.png"], embedding=np.eye(1,512,0,dtype=np.float32)[0],
                          size=len(content["a.png"]), mtime=1)
    content["a.png"] = _png("blue")
    monkeypatch.setattr(media, "list_entries", lambda root: ([media.MediaEntry(root, "a.png", len(content["a.png"]), 2)], True))
    calls = {"n": 0}
    def changing_stat(root, path):
        calls["n"] += 1
        return media.MediaEntry(root, path, len(content[path]), 2 if calls["n"] == 1 else 3)
    monkeypatch.setattr(media, "stat_entry", changing_stat)
    result = scan.scan_root("r", rebuild=True)
    assert result["status"] == "partial"
    assert store.get("r", "a.png")["content_version"] == old["content_version"]
