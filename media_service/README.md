# ai-media

本机公众号归档与图片语义检索服务。设计与验收口径见 [整合规格](docs/specs/integration.md)。

## 启动

需要 Python 3.12、`uv`、本机 Chrome；首次模型加载需要下载 Chinese-CLIP 权重。

```sh
./run.sh
```

打开 `http://127.0.0.1:8765/`。端口可用 `WX_PORT` 指定；媒体根可用 `AI_MEDIA_ROOTS` 的 JSON 数组配置，每项含 `id`、`type`（`local`/`zspace`）、绝对 `path` 和 `writable`。默认仅 NAS 归档根可写。`AI_MEDIA_DATA_DIR` 指向 SQLite、任务及缩略图的本地持久目录。敏感认证信息由 zspace-cli 已登录 profile 管理。

## 验证

```sh
uv run --extra test python -m pytest tests/
uv run python -m compileall app
```

首次扫描与模型推理会花时间。三个来源项目（wx-archiver、nas-image-search、zspace-photo-search）已于 2026-11-08 整合完成后删除；其旧索引（13,043 条）备份在 `data/legacy-import/index.sqlite`。服务仅监听本机地址。

## 当前状态

已完成本机 local root 的扫描、检索和预览贯通，归档以假浏览器素材验证。已用一篇真实文章在独立 NAS 测试目录完成归档→检索→预览→ZIP 与只读重扫；断连/验证页、旧库批量导入、全库模型升级、HEIC/NSFW 和三万图性能仍待验收或补齐；详情见整合规格的“本地实施记录”。三个来源项目已删除，删除后 `uv run pytest tests/` 40 项全过、`compileall app` 通过（2026-11-08）。
