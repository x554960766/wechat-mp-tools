/**
 * 全局文件检索与查重（全局工作台）。
 *
 * 只读媒体服务已入库索引：文件清单来自 images / standalone_texts / videos 三表联合，
 * 重复判定基于内容摘要 content_sha256（同摘要跨根视为重复）。不做磁盘扫描、不重新哈希。
 *
 * 图片行提供「普通查看」与「VR 全景」：两者都复用共享 MediaViewer（前缀 fv），
 * 不复制一套查看器逻辑，页面只负责把当前图片集合与索引交给它。
 * 文本/视频只展示元数据；视频可打开原片（版本取内容摘要，与媒体服务播放接口口径一致）。
 */
const FilesPage = {
    _page: 0,
    _limit: 50,
    _total: 0,
    _items: [],
    _viewer: null,
    _viewerImages: [],
    _dupGroups: [],
    _dupTotal: 0,
    _roots: {},

    esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    },

    /** 缩略图地址。只对图片可用：缩略服务要求索引记录存在且版本一致。
     *  尺寸必须用 360：媒体服务缩略缓存只实现了 size=360，其他尺寸返回 422 invalid_size，会导致回显破图。 */
    _thumbUrl(item) {
        return `/media/api/media/thumb?root=${encodeURIComponent(item.root || '')}`
            + `&relpath=${encodeURIComponent(item.relpath || '')}`
            + `&version=${encodeURIComponent(item.content_version != null ? item.content_version : '')}`
            + '&size=360';
    },

    /** 图片原始查看地址（普通查看走这里）。 */
    _fileUrl(item) {
        return `/media/api/media/file?root=${encodeURIComponent(item.root || '')}`
            + `&relpath=${encodeURIComponent(item.relpath || '')}`
            + `&version=${encodeURIComponent(item.content_version != null ? item.content_version : '')}`
            + '&display=1';
    },

    /** 视频播放地址。媒体服务用 version 承载内容摘要做防串校验，故版本取 content_sha256。 */
    _videoUrl(item) {
        return `/media/api/media/video?root=${encodeURIComponent(item.root || '')}`
            + `&relpath=${encodeURIComponent(item.relpath || '')}`
            + `&version=${encodeURIComponent(item.content_sha256 || '')}`;
    },

    _formatSize(bytes) {
        if (bytes == null) return '—';
        const n = Number(bytes);
        if (!Number.isFinite(n) || n < 0) return '—';
        if (n < 1024) return `${n} B`;
        const units = ['KB', 'MB', 'GB', 'TB'];
        let value = n / 1024;
        let i = 0;
        while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
        return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
    },

    _kindLabel(kind) {
        if (kind === 'image') return '图片';
        if (kind === 'text') return '文本';
        if (kind === 'video') return '视频';
        return kind || '—';
    },

    _kindIcon(kind) {
        if (kind === 'image') return '🖼️';
        if (kind === 'text') return '📄';
        if (kind === 'video') return '🎬';
        return '📁';
    },

    /** 修改时间：兼容秒与毫秒时间戳，空值显示占位符。 */
    _formatMtime(mtime) {
        if (mtime == null || mtime === '') return '—';
        const n = Number(mtime);
        if (!Number.isFinite(n)) return this.esc(String(mtime));
        const date = new Date(n < 1e12 ? n * 1000 : n);
        const pad = (v) => String(v).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
            + ` ${pad(date.getHours())}:${pad(date.getMinutes())}`;
    },

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">全局文件检索</h2>
                <p class="page-description">
                    跨全部媒体根检索已入库文件，并按内容摘要识别重复。图片支持普通查看与 VR 全景，文本与视频只展示元数据。
                </p>
            </div>

            <div class="files-grid">
                <div class="card">
                    <div class="card-header"><h3 class="card-title">清单概览</h3></div>
                    <div id="files-stats-body"><p class="form-hint">加载中…</p></div>
                </div>
                <div class="card">
                    <div class="card-header"><h3 class="card-title">重复情况</h3></div>
                    <div id="files-dups-body"><p class="form-hint">加载中…</p></div>
                </div>
            </div>

            <div class="card">
                <div class="card-header"><h3 class="card-title">检索条件</h3></div>
                <div class="files-grid">
                    <div class="form-group">
                        <label class="form-label" for="files-q">文件名关键词</label>
                        <input class="form-input" id="files-q" type="text" placeholder="按相对路径模糊匹配，回车检索">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="files-root">媒体根</label>
                        <select class="form-input" id="files-root">
                            <option value="">全部根</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="files-ext">扩展名</label>
                        <input class="form-input" id="files-ext" type="text" placeholder="如 jpg、mp4">
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="files-kind">类型</label>
                        <select class="form-input" id="files-kind">
                            <option value="">全部</option>
                            <option value="image">图片</option>
                            <option value="text">文本</option>
                            <option value="video">视频</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="files-dup">重复状态</label>
                        <select class="form-input" id="files-dup">
                            <option value="">全部</option>
                            <option value="duplicates">只看重复</option>
                            <option value="unique">只看唯一</option>
                        </select>
                    </div>
                    <div class="form-group" style="justify-content:flex-end;">
                        <div class="btn-group">
                            <button class="btn btn-primary" id="files-search">检索</button>
                            <button class="btn btn-secondary" id="files-reset">重置</button>
                        </div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">文件清单</h3>
                    <span class="form-hint" id="files-count">—</span>
                </div>
                <div id="files-list"><div class="empty-state"><p class="form-hint">加载中…</p></div></div>
                <div class="btn-group" id="files-pager">
                    <button class="btn btn-secondary" id="files-prev">上一页</button>
                    <span class="form-hint" id="files-page">1 / 1</span>
                    <button class="btn btn-secondary" id="files-next">下一页</button>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">重复内容组</h3>
                    <button class="btn btn-secondary btn-sm" id="files-load-dups">刷新重复组</button>
                </div>
                <div id="files-dups"><p class="form-hint">点击「刷新重复组」查看按内容摘要归并的重复文件。</p></div>
            </div>
        `;
    },

    async init() {
        this._page = 0;
        this._items = [];
        this._dupGroups = [];
        this._viewer = new MediaViewer('fv');

        const searchBtn = document.getElementById('files-search');
        if (searchBtn) searchBtn.addEventListener('click', () => this._search());
        const resetBtn = document.getElementById('files-reset');
        if (resetBtn) resetBtn.addEventListener('click', () => this._reset());
        const qInput = document.getElementById('files-q');
        if (qInput) qInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this._search();
        });
        const rootSel = document.getElementById('files-root');
        if (rootSel) rootSel.addEventListener('change', () => this._search());
        const kindSel = document.getElementById('files-kind');
        if (kindSel) kindSel.addEventListener('change', () => this._search());
        const dupSel = document.getElementById('files-dup');
        if (dupSel) dupSel.addEventListener('change', () => this._search());
        const extInput = document.getElementById('files-ext');
        if (extInput) extInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this._search();
        });
        const prevBtn = document.getElementById('files-prev');
        if (prevBtn) prevBtn.addEventListener('click', () => this._goPage(this._page - 1));
        const nextBtn = document.getElementById('files-next');
        if (nextBtn) nextBtn.addEventListener('click', () => this._goPage(this._page + 1));

        // 列表内的查看/播放动作用事件委托：清单每次重渲染，逐个绑定会在 DOM 替换后失效。
        const listEl = document.getElementById('files-list');
        if (listEl) listEl.addEventListener('click', (event) => this._onListClick(event));
        const dupsEl = document.getElementById('files-dups');
        if (dupsEl) dupsEl.addEventListener('click', (event) => this._onDupClick(event));

        const loadBtn = document.getElementById('files-load-dups');
        if (loadBtn) loadBtn.addEventListener('click', () => this._loadDuplicates());

        await this._loadRoots();
        // 根列表需先于清单与重复组就绪：删除按钮要按根的可写性渲染，否则首次渲染会漏掉按钮。
        await Promise.allSettled([this._loadStats(), this._loadList()]);
        await this._loadDuplicates();
    },

    onShow() {
        // 从别的页返回时刷新概览与清单：索引可能在别处被扫描更新过。
        Promise.allSettled([this._loadStats(), this._loadList()]);
    },

    destroy() {
        if (this._viewer) {
            this._viewer.close();
            this._viewer = null;
        }
        this._items = [];
        this._viewerImages = [];
    },

    async _loadRoots() {
        const sel = document.getElementById('files-root');
        if (!sel) return;
        try {
            const data = await API.media.roots();
            const items = (data && data.items) || [];
            this._roots = {};
            items.forEach((root) => {
                // 记录是否可写：删除按钮只对可写根开放，避免前端点了才被后端 403 拒绝。
                this._roots[root.id] = { label: root.label || root.id, writable: !!root.writable };
                const opt = document.createElement('option');
                opt.value = root.id;
                opt.textContent = root.label || root.id;
                sel.appendChild(opt);
            });
        } catch (err) {
            // 根列表取不到不影响检索：留空即「全部根」，不必让用户卡在这。
            console.warn('加载媒体根失败：', err);
        }
    },

    async _loadStats() {
        const body = document.getElementById('files-stats-body');
        if (!body) return;
        try {
            const s = await API.files.stats();
            const kindBits = (s.kinds || [])
                .map((k) => `<span class="files-pill">${this.esc(this._kindLabel(k.kind))} ${this.esc(k.count)}</span>`)
                .join(' ');
            const rootBits = (s.roots || [])
                .map((r) => `<span class="files-pill">${this.esc(r.root)} ${this.esc(r.count)}</span>`)
                .join(' ');
            body.innerHTML = `
                <div class="files-stats-rows">
                    <div class="files-stat"><span class="files-stat-value">${this.esc(s.total)}</span><span class="files-stat-label">已入库文件</span></div>
                    <div class="files-stat"><span class="files-stat-value">${this.esc(this._formatSize(s.biggest && s.biggest.size))}</span><span class="files-stat-label">最大文件</span></div>
                </div>
                <div class="files-stats-block">
                    <div class="files-block-title">按类型</div>
                    <div class="files-pills">${kindBits || '<span class="form-hint">—</span>'}</div>
                </div>
                <div class="files-stats-block">
                    <div class="files-block-title">按媒体根</div>
                    <div class="files-pills">${rootBits || '<span class="form-hint">—</span>'}</div>
                </div>
            `;
        } catch (err) {
            body.innerHTML = '<p class="form-hint" style="color:var(--error)">概览加载失败：' + this.esc(err.message || err) + '</p>';
        }
    },

    async _loadList() {
        const listEl = document.getElementById('files-list');
        const countEl = document.getElementById('files-count');
        const pageEl = document.getElementById('files-page');
        const pagerEl = document.getElementById('files-pager');
        if (!listEl) return;
        const params = {
            q: (document.getElementById('files-q') || {}).value || undefined,
            root: (document.getElementById('files-root') || {}).value || undefined,
            ext: (document.getElementById('files-ext') || {}).value || undefined,
            kind: (document.getElementById('files-kind') || {}).value || undefined,
            duplicates: (document.getElementById('files-dup') || {}).value || undefined,
            limit: this._limit,
            offset: this._page * this._limit,
        };
        try {
            const data = await API.files.list(params, { showError: false });
            this._items = (data && data.items) || [];
            this._total = (data && data.total) || 0;
            this._viewerImages = this._items.filter((item) => item.kind === 'image');
            this._renderList();
        } catch (err) {
            this._items = [];
            listEl.innerHTML = `<div class="empty-state"><p class="form-hint" style="color:var(--error)">文件清单加载失败：${this.esc(err.message || err)}</p></div>`;
        }
        if (countEl) countEl.textContent = `共 ${this._total} 条`;
        const totalPages = Math.max(1, Math.ceil(this._total / this._limit));
        if (pageEl) pageEl.textContent = `${this._page + 1} / ${totalPages}`;
        if (pagerEl) {
            pagerEl.style.display = this._total > this._limit ? 'flex' : 'none';
        }
    },

    _renderList() {
        const listEl = document.getElementById('files-list');
        if (!listEl) return;
        if (!this._items.length) {
            listEl.innerHTML = '<div class="empty-state"><p class="empty-state-title">没有匹配的文件</p></div>';
            return;
        }
        listEl.innerHTML = this._items.map((item, index) => {
            const dupBadge = Number(item.dup_count) > 1
                ? `<span class="files-badge files-badge-dup">重复 ×${this.esc(item.dup_count)}</span>`
                : `<span class="files-badge files-badge-ok">唯一</span>`;
            const thumb = item.kind === 'image'
                ? `<img class="files-thumb" src="${this.esc(this._thumbUrl(item))}" loading="lazy" alt="">`
                : `<span class="files-thumb files-thumb-icon">${this._kindIcon(item.kind)}</span>`;
            const actions = item.kind === 'image'
                ? `<button class="btn btn-secondary btn-sm" data-act="view" data-index="${index}">普通查看</button>
                   <button class="btn btn-secondary btn-sm" data-act="pano" data-index="${index}">VR 全景</button>`
                : (item.kind === 'video'
                    ? `<a class="btn btn-secondary btn-sm" href="${this.esc(this._videoUrl(item))}" target="_blank" rel="noopener">播放</a>`
                    : '');
            // 删除为破坏性操作，仅对可写媒体根开放；只读根不显示按钮，避免点了才被拒绝。
            const rootInfo = this._roots[item.root];
            const delBtn = (rootInfo && rootInfo.writable)
                ? `<button class="btn btn-danger btn-sm" data-act="delete" data-root="${this.esc(item.root)}" data-relpath="${this.esc(item.relpath)}">删除</button>`
                : '';
            const sha = item.content_sha256 ? this.esc(item.content_sha256.slice(0, 12)) : '—';
            return `
                <div class="files-row">
                    ${thumb}
                    <div class="files-main">
                        <div class="files-path" title="${this.esc(item.relpath)}">${this.esc(item.relpath)}</div>
                        <div class="files-meta">
                            <span class="files-badge">${this.esc(this._kindLabel(item.kind))}</span>
                            <span class="files-meta-item">根：${this.esc(item.root)}</span>
                            <span class="files-meta-item">大小：${this.esc(this._formatSize(item.size))}</span>
                            <span class="files-meta-item">摘要：${sha}</span>
                        </div>
                        <div class="files-actions">${dupBadge}${actions}${delBtn}</div>
                    </div>
                </div>`;
        }).join('');
    },

    _onListClick(event) {
        const act = event.target.closest('[data-act]');
        if (!act) return;
        const index = Number(act.dataset.index);
        if (act.dataset.act === 'view') this._openViewer(index);
        else if (act.dataset.act === 'pano') this._openViewer(index, true);
        else if (act.dataset.act === 'delete') this._confirmDelete(act.dataset.root, act.dataset.relpath);
    },

    /** 删除前二次确认；确认后先删物理文件、再清索引，两者都成功才刷新。 */
    _confirmDelete(root, relpath) {
        const rootInfo = this._roots[root] || { label: root };
        Modal.confirm(
            '删除文件',
            `将永久删除：<br><code>${this.esc(rootInfo.label)} / ${this.esc(relpath)}</code><br><br>`
            + '<strong style="color:var(--error)">同时删除磁盘文件与索引记录，此操作不可恢复。</strong>',
            async () => { await this._doDelete(root, relpath); },
        );
    },

    async _doDelete(root, relpath) {
        try {
            await API.files.deleteFile({ root, relpath }, { showError: false });
            Toast.success(`已删除 ${root} / ${relpath}`);
            // 删除会同时改变清单、重复组与统计，三处一起刷新。
            await Promise.allSettled([this._loadStats(), this._loadList()]);
            await this._loadDuplicates();
        } catch (err) {
            Toast.error('删除失败：' + (err.message || err));
        }
    },

    _openViewer(index, pano = false) {
        const images = this._viewerImages;
        if (!this._viewer || !images.length) return;
        // 把当前图片集中的第 index 项映射到查看器序列：清单里的 index 是全类型行号，需换算。
        const imageRow = this._items[index];
        if (!imageRow) return;
        let imageIndex = images.findIndex((image) => image.root === imageRow.root && image.relpath === imageRow.relpath);
        if (imageIndex < 0) imageIndex = 0;
        this._viewer.open(images, imageIndex, {});
        if (pano) this._viewer.panorama();
    },

    _goPage(page) {
        const totalPages = Math.max(1, Math.ceil(this._total / this._limit));
        this._page = Math.max(0, Math.min(totalPages - 1, page));
        this._loadList();
    },

    _search() {
        this._page = 0;
        this._loadList();
    },

    _reset() {
        const q = document.getElementById('files-q'); if (q) q.value = '';
        const root = document.getElementById('files-root'); if (root) root.value = '';
        const ext = document.getElementById('files-ext'); if (ext) ext.value = '';
        const kind = document.getElementById('files-kind'); if (kind) kind.value = '';
        const dup = document.getElementById('files-dup'); if (dup) dup.value = '';
        this._search();
    },

    async _loadDuplicates() {
        const body = document.getElementById('files-dups-body');
        if (body) body.innerHTML = '<p class="form-hint">加载中…</p>';
        const wrap = document.getElementById('files-dups');
        if (!wrap) return;
        try {
            const data = await API.files.duplicates({ min: 2, limit: 50 }, { showError: false });
            this._dupGroups = (data && data.items) || [];
            this._dupTotal = (data && data.total) || 0;
            this._renderDuplicates();
        } catch (err) {
            wrap.innerHTML = `<p class="form-hint" style="color:var(--error)">重复组加载失败：${this.esc(err.message || err)}</p>`;
        }
        if (body) {
            const s = await API.files.stats().catch(() => null);
            if (s) {
                body.innerHTML = `
                    <div class="files-stats-rows">
                        <div class="files-stat"><span class="files-stat-value">${this.esc(s.duplicate_groups)}</span><span class="files-stat-label">重复内容组</span></div>
                        <div class="files-stat"><span class="files-stat-value">${this.esc(s.duplicate_files)}</span><span class="files-stat-label">涉及文件</span></div>
                    </div>
                    <p class="form-hint">内容摘要相同即视为重复，跨媒体根也一并归组。</p>`;
            }
        }
    },

    _renderDuplicates() {
        const wrap = document.getElementById('files-dups');
        if (!wrap) return;
        if (!this._dupGroups.length) {
            wrap.innerHTML = '<div class="empty-state"><p class="empty-state-title">没有发现重复内容</p></div>';
            return;
        }
        wrap.innerHTML = this._dupGroups.map((group, index) => `
            <div class="files-dup-group">
                <div class="files-dup-head">
                    <span class="files-dup-sha" title="${this.esc(group.sha)}">${this.esc(group.sha.slice(0, 16))}…</span>
                    <span class="files-badge files-badge-dup">${this.esc(group.count)} 份</span>
                    <span class="files-badge">${this.esc(this._formatSize(group.size))}</span>
                    <button class="btn btn-secondary btn-sm" data-act="expand" data-index="${index}">展开全部</button>
                </div>
                <div class="files-dup-members">
                    <table class="files-dup-table">
                        <thead>
                            <tr><th>类型</th><th>媒体根</th><th>相对路径</th><th>大小</th><th>修改时间</th><th>操作</th></tr>
                        </thead>
                        <tbody>
                            ${(group.members || []).map((member) => this._dupMember(member)).join('')}
                        </tbody>
                    </table>
                </div>
                <div class="files-dup-more" data-more="${index}" hidden></div>
            </div>
        `).join('');
    },

    /** 重复组成员行：同摘要各副本按同一套列并排展示，便于横向对比来源与路径。 */
    _dupMember(member) {
        const rootInfo = this._roots[member.root] || { label: member.root, writable: false };
        const delBtn = rootInfo.writable
            ? `<button class="btn btn-danger btn-sm" data-act="delete" data-root="${this.esc(member.root)}" data-relpath="${this.esc(member.relpath)}">删除</button>`
            : '<span class="form-hint">只读</span>';
        return `<tr>
            <td>${this.esc(this._kindLabel(member.kind))}</td>
            <td>${this.esc(rootInfo.label)}</td>
            <td class="files-dup-path" title="${this.esc(member.relpath)}">${this.esc(member.relpath)}</td>
            <td>${this.esc(this._formatSize(member.size))}</td>
            <td>${this.esc(this._formatMtime(member.mtime))}</td>
            <td class="files-dup-op">${delBtn}</td>
        </tr>`;
    },

    async _onDupClick(event) {
        const act = event.target.closest('[data-act]');
        if (!act) return;
        if (act.dataset.act === 'delete') {
            this._confirmDelete(act.dataset.root, act.dataset.relpath);
            return;
        }
        if (act.dataset.act !== 'expand') return;
        const index = Number(act.dataset.index);
        const group = this._dupGroups[index];
        const more = document.querySelector(`[data-more="${index}"]`);
        if (!group || !more) return;
        act.disabled = true;
        try {
            const data = await API.files.group(group.sha, { showError: false });
            const members = (data && data.members) || [];
            more.innerHTML = '<table class="files-dup-table"><thead><tr>'
                + '<th>类型</th><th>媒体根</th><th>相对路径</th><th>大小</th><th>修改时间</th><th>操作</th>'
                + '</tr></thead><tbody>'
                + members.map((member) => this._dupMember(member)).join('')
                + '</tbody></table>';
            more.hidden = false;
        } catch (err) {
            more.innerHTML = `<p class="form-hint" style="color:var(--error)">展开失败：${this.esc(err.message || err)}</p>`;
            more.hidden = false;
        } finally {
            act.disabled = false;
            act.textContent = '展开全部';
        }
    },
};

window.FilesPage = FilesPage;
