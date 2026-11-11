/**
 * 素材检索页面：在媒体服务已索引的公众号图片里按文字描述检索。
 *
 * 所有请求都走宿主的 /media 反代，本页不认识媒体服务的端口。
 * 结果里的 thumb/file 是媒体服务内部路径，必须由 API.media 补 /media 前缀。
 */
const MediaSearchPage = {
    _items: [],
    _index: -1,
    _roots: [],
    _scanTimer: null,
    _keyHandler: null,
    _original: false,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">素材检索</h2>
                <p class="page-description">在已下载文章的配图里按文字描述找图。多个短语用空格分隔，命中更精确。</p>
            </div>

            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="media-search-bar">
                    <input type="text" class="form-input" id="ms-q" autocomplete="off"
                           placeholder="例如：雪山 日落 / 蓝色 海报 / 咖啡 手写">
                    <button class="btn btn-primary" id="ms-go">检索</button>
                </div>

                <details class="media-advanced">
                    <summary>高级筛选</summary>
                    <div class="grid grid-2" style="gap: 12px; margin-top: 12px;">
                        <div class="form-group">
                            <label class="form-label" for="ms-neg">排除词</label>
                            <input type="text" class="form-input" id="ms-neg" placeholder="不想要的内容，空格分隔">
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ms-k">返回数量</label>
                            <select class="form-input" id="ms-k">
                                <option value="24">24 张</option>
                                <option value="48">48 张</option>
                                <option value="96">96 张</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ms-after">首次入库（起）</label>
                            <input type="date" class="form-input" id="ms-after">
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ms-before">首次入库（止）</label>
                            <input type="date" class="form-input" id="ms-before">
                        </div>
                    </div>
                    <label class="media-check">
                        <input type="checkbox" id="ms-review">
                        <span>同时跑 NSFW 复核（会逐张读原图，慢很多）</span>
                    </label>
                </details>

                <div class="media-roots" id="ms-roots">
                    <span class="form-hint">正在读取媒体根…</span>
                </div>

                <div class="media-toolbar">
                    <span class="form-hint" id="ms-stats"></span>
                    <span class="media-toolbar-right">
                        <span class="form-hint" id="ms-scan-state"></span>
                        <button class="btn btn-secondary btn-sm" id="ms-scan">重新扫描</button>
                    </span>
                </div>
            </div>

            <div id="ms-results" class="media-results">
                <div class="empty-state">
                    <div class="empty-state-icon">🔍</div>
                    <h3 class="empty-state-title">输入描述开始检索</h3>
                    <p class="empty-state-desc">索引来自 wmp 下载文章时落盘的配图，新下载的文章会自动入库。</p>
                </div>
            </div>
        `;
    },

    async init() {
        this._items = [];
        this._index = -1;
        this._original = false;

        const q = document.getElementById('ms-q');
        if (q) {
            q.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.search();
            });
        }
        const go = document.getElementById('ms-go');
        if (go) go.addEventListener('click', () => this.search());
        const scan = document.getElementById('ms-scan');
        if (scan) scan.addEventListener('click', () => this.triggerScan());
        const results = document.getElementById('ms-results');
        if (results) {
            results.addEventListener('click', (e) => {
                const card = e.target.closest('.media-card');
                if (card) this.openLightbox(Number(card.dataset.index));
            });
        }

        await this.loadRoots();
        this.loadStats();
    },

    destroy() {
        clearInterval(this._scanTimer);
        this._scanTimer = null;
        this.detachKeys();
    },

    // ── 媒体根与统计 ──────────────────────────────────
    async loadRoots() {
        const box = document.getElementById('ms-roots');
        try {
            const data = await API.media.roots();
            this._roots = (data && data.items) || [];
        } catch (err) {
            if (box) {
                box.innerHTML = `<span class="media-error">媒体服务不可用（${err.status || '网络'}）：${this.esc(err.message)}</span>`;
            }
            return;
        }
        if (!box) return;
        if (!this._roots.length) {
            box.innerHTML = '<span class="form-hint">媒体服务没有配置任何媒体根</span>';
            return;
        }
        box.innerHTML = '<span class="media-roots-label">来源：</span>' + this._roots.map((r) => {
            const state = r.reachable === true ? '在线' : (r.reachable === false ? '离线' : '未知');
            return `<label class="media-chip" title="${state}">
                        <input type="checkbox" value="${this.esc(r.id)}" checked>
                        <span>${this.esc(r.label || r.id)}</span>
                    </label>`;
        }).join('');
    },

    async loadStats() {
        const el = document.getElementById('ms-stats');
        if (!el) return;
        try {
            const stats = await API.media.stats();
            el.textContent = `已索引 ${stats.total || 0} 张图片`;
        } catch (err) {
            el.textContent = '索引统计不可用';
        }
    },

    selectedRoots() {
        const box = document.getElementById('ms-roots');
        if (!box) return [];
        return Array.from(box.querySelectorAll('input[type="checkbox"]:checked')).map((i) => i.value);
    },

    // ── 检索 ─────────────────────────────────────────
    async search() {
        const q = (document.getElementById('ms-q')?.value || '').trim();
        if (!q) {
            Toast.error('请输入要检索的描述');
            return;
        }
        const roots = this.selectedRoots();
        if (!roots.length) {
            Toast.error('至少选择一个来源');
            return;
        }
        const payload = {
            q,
            neg: (document.getElementById('ms-neg')?.value || '').trim(),
            roots,
            k: Number(document.getElementById('ms-k')?.value || 24),
            review: !!(document.getElementById('ms-review')?.checked),
            after: document.getElementById('ms-after')?.value || null,
            before: document.getElementById('ms-before')?.value || null,
        };

        const results = document.getElementById('ms-results');
        if (results) {
            results.innerHTML = `<div class="loading-screen"><div class="spinner"></div><p>检索中…</p></div>`;
        }
        try {
            const data = await API.media.searchText(payload);
            this._items = (data && data.items) || [];
            this._index = -1;
            this.renderResults();
        } catch (err) {
            this._items = [];
            this.renderResults(err);
        }
    },

    renderResults(err) {
        const box = document.getElementById('ms-results');
        if (!box) return;
        if (err) {
            const code = err.status ? `HTTP ${err.status}` : '请求失败';
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">⚠️</div>
                    <h3 class="empty-state-title">检索失败（${code}）</h3>
                    <p class="empty-state-desc">${this.esc(err.message || '')}</p>
                </div>`;
            return;
        }
        if (!this._items.length) {
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🫧</div>
                    <h3 class="empty-state-title">没有匹配的图片</h3>
                    <p class="empty-state-desc">换个说法，或确认负责采集的来源是否在线。新下载的文章需要先扫描入库。</p>
                </div>`;
            return;
        }
        const esc = (v) => this.esc(v);
        box.innerHTML = `<div class="media-grid">${this._items.map((item, i) => {
            const badges = [];
            if (item.source_state === 'offline') badges.push('<span class="media-badge is-warn">离线</span>');
            if (item.preview_cached) badges.push('<span class="media-badge">已缓存</span>');
            if (item.article && item.article.title) badges.push('<span class="media-badge is-soft">原文</span>');
            const title = item.article && item.article.title ? item.article.title : item.relpath.split('/').pop();
            const date = (item.ingested_at || '').slice(0, 10);
            return `<button type="button" class="media-card" data-index="${i}">
                        <span class="media-card-thumb">
                            <img src="${esc(API.media.thumb(item))}" loading="lazy" alt=""
                                 onerror="this.closest('.media-card').classList.add('is-broken')">
                            ${badges.join('')}
                        </span>
                        <span class="media-card-meta">
                            <span class="media-card-title">${esc(title)}</span>
                            <span class="media-card-sub">${esc(item.source_label || item.root)} · ${esc(date)}</span>
                        </span>
                    </button>`;
        }).join('')}</div>`;
    },

    // ── 灯箱 ─────────────────────────────────────────
    openLightbox(index) {
        if (!Number.isInteger(index) || index < 0 || index >= this._items.length) return;
        this._index = index;
        this._original = false;
        this.mountLightbox();
        this.attachKeys();
    },

    mountLightbox() {
        const item = this._items[this._index];
        if (!item) return;
        const total = this._items.length;
        const title = item.article && item.article.title
            ? this.esc(item.article.title)
            : this.esc(item.relpath.split('/').pop());
        const original = this._original ? API.media.original(item) : API.media.display(item);
        const article = item.article || {};
        Modal.open({
            title: `${title}　<span class="media-counter">${this._index + 1}/${total}</span>`,
            content: `
                <div class="media-lightbox">
                    <img id="ms-lightbox-img" src="${this.esc(original)}" alt="">
                    <div class="media-lightbox-meta">
                        <div>${this.esc(item.source_label || item.root)} · 相关度 ${item.score.toFixed(3)} · 入库 ${this.esc((item.ingested_at || '').slice(0, 10))}</div>
                        <div class="media-path">${this.esc(item.relpath)}</div>
                        ${article.url ? `<div><a href="${this.esc(article.url)}" target="_blank" rel="noopener">打开原文</a></div>` : ''}
                    </div>
                </div>`,
            footer: `
                <button class="btn btn-secondary" onclick="MediaSearchPage.step(-1)">← 上一张</button>
                <button class="btn btn-secondary" onclick="MediaSearchPage.toggleOriginal()" id="ms-orig-btn">${this._original ? '看缩略图' : '看原图'}</button>
                <button class="btn btn-secondary" onclick="MediaSearchPage.copyPath()">复制路径</button>
                <button class="btn btn-primary" onclick="MediaSearchPage.step(1)">下一张 →</button>
            `,
            onClose: () => this.detachKeys(),
        });
    },

    step(delta) {
        const next = this._index + delta;
        if (next < 0 || next >= this._items.length) return;
        this._index = next;
        this._original = false;
        this.mountLightbox();
    },

    toggleOriginal() {
        this._original = !this._original;
        this.mountLightbox();
    },

    copyPath() {
        const item = this._items[this._index];
        if (!item) return;
        const text = item.relpath;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(
                () => Toast.success('路径已复制'),
                () => Toast.info(text),
            );
        } else {
            Toast.info(text);
        }
    },

    attachKeys() {
        this.detachKeys();
        this._keyHandler = (e) => {
            if (!document.getElementById('ms-lightbox-img')) return;
            if (e.key === 'ArrowLeft') { e.preventDefault(); this.step(-1); }
            else if (e.key === 'ArrowRight') { e.preventDefault(); this.step(1); }
        };
        document.addEventListener('keydown', this._keyHandler);
    },

    detachKeys() {
        if (this._keyHandler) {
            document.removeEventListener('keydown', this._keyHandler);
            this._keyHandler = null;
        }
    },

    // ── 扫描 ─────────────────────────────────────────
    async triggerScan() {
        const state = document.getElementById('ms-scan-state');
        if (state) state.textContent = '正在提交扫描…';
        try {
            const job = await API.media.scan('articles-full', false);
            Toast.info('扫描任务已提交');
            this.pollScan(job.id);
        } catch (err) {
            if (state) state.textContent = '';
            Toast.error(`扫描提交失败: ${err.message}`);
        }
    },

    pollScan(jobId) {
        clearInterval(this._scanTimer);
        const tick = async () => {
            const state = document.getElementById('ms-scan-state');
            try {
                const job = await API.media.scanStatus(jobId);
                if (state) {
                    state.textContent = `扫描中 ${job.scanned || 0} 个${job.current ? '：' + job.current : ''}`;
                }
                if (['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
                    clearInterval(this._scanTimer);
                    this._scanTimer = null;
                    if (state) {
                        state.textContent = job.status === 'completed'
                            ? `扫描完成：新增 ${job.indexed_new || 0}、更新 ${job.indexed_updated || 0}`
                            : `扫描结束（${job.status}）`;
                    }
                    this.loadStats();
                }
            } catch (err) {
                clearInterval(this._scanTimer);
                this._scanTimer = null;
                if (state) state.textContent = '扫描状态读取失败';
            }
        };
        this._scanTimer = setInterval(tick, 1200);
        tick();
    },

    esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    },
};
