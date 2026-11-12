/**
 * 素材检索：检索粒度是「内容条目」——公众号文章、视频号/抖音/快手/B站视频、
 * 小红书笔记——而不是单张图片。标题、正文、账号名都进全文索引。
 *
 * 所有请求都走宿主的 /media 反代，本页不认识媒体服务的端口；后端返回的
 * thumb/file 是媒体服务内部路径，必须由 API.media 补 /media 前缀。
 */
const MediaSearchPage = {
    _items: [],
    _total: 0,
    _limit: 24,
    _offset: 0,
    _query: '',
    _platform: '',
    _account: '',
    _root: '',
    _roots: [],
    _facets: { platforms: [], accounts: [], total: 0 },
    _scanTimer: null,
    _keyHandler: null,
    _detail: null,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">素材检索</h2>
                <p class="page-description">按标题、正文和账号检索已下载的内容：公众号文章、视频号/抖音/快手/B站视频、小红书笔记。留空直接看最近入库。</p>
            </div>

            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="media-search-bar">
                    <input type="text" class="form-input" id="ms-q" autocomplete="off"
                           placeholder="例如：手机壁纸 / 翰墨修心 / 咖啡 手写">
                    <button class="btn btn-primary" id="ms-go">检索</button>
                </div>

                <div class="media-roots" id="ms-platforms">
                    <span class="form-hint">正在读取平台…</span>
                </div>

                <div class="grid grid-2" style="gap: 12px;">
                    <div class="form-group">
                        <label class="form-label" for="ms-account">账号 / UP主</label>
                        <select class="form-input" id="ms-account"><option value="">全部账号</option></select>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="ms-root">媒体来源（扫描用）</label>
                        <select class="form-input" id="ms-root"><option value="">读取中…</option></select>
                    </div>
                </div>

                <div class="media-toolbar">
                    <span class="form-hint" id="ms-stats"></span>
                    <span class="media-toolbar-right">
                        <span class="form-hint" id="ms-scan-state"></span>
                        <button class="btn btn-secondary btn-sm" id="ms-scan">扫描选中来源</button>
                    </span>
                </div>
            </div>

            <div id="ms-results" class="media-results">
                <div class="empty-state">
                    <div class="empty-state-icon">🔍</div>
                    <h3 class="empty-state-title">正在读取索引…</h3>
                    <p class="empty-state-desc">索引来自各平台下载目录，扫描一次之后新内容会持续入库。</p>
                </div>
            </div>
            <div class="content-pager" id="ms-pager" hidden></div>
        `;
    },

    async init(params) {
        this._items = [];
        this._total = 0;
        this._offset = 0;
        this._platform = '';
        this._account = '';
        this._query = '';

        // 侧栏全局检索跳转携带的关键词：预填输入框，随后的 search() 会读取它
        const q = document.getElementById('ms-q');
        if (q && params && typeof params.q === 'string') {
            q.value = params.q;
        }
        if (q) {
            q.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.search(true);
            });
        }
        const go = document.getElementById('ms-go');
        if (go) go.addEventListener('click', () => this.search(true));
        const scan = document.getElementById('ms-scan');
        if (scan) scan.addEventListener('click', () => this.triggerScan());
        const account = document.getElementById('ms-account');
        if (account) {
            account.addEventListener('change', () => {
                this._account = account.value;
                this.search(true);
            });
        }
        await this.loadFacets();
        await this.loadRoots();
        this.loadStats();
        await this.search(true);
    },

    destroy() {
        clearInterval(this._scanTimer);
        this._scanTimer = null;
        this.detachKeys();
    },

    // ── 平台 / 账号筛选 ────────────────────────────────
    async loadFacets() {
        try {
            const data = await API.media.contentsFacets();
            this._facets = {
                platforms: (data && data.platforms) || [],
                accounts: (data && data.accounts) || [],
                total: (data && data.total) || 0,
            };
        } catch (err) {
            this._facets = { platforms: [], accounts: [], total: 0 };
        }
        this.renderPlatforms();
        this.renderAccounts();
    },

    renderPlatforms() {
        const box = document.getElementById('ms-platforms');
        if (!box) return;
        const items = this._facets.platforms || [];
        const total = this._facets.total || 0;
        const chip = (value, label, count, active) => `
            <button type="button" class="media-chip${active ? ' is-active' : ''}" data-platform="${this.esc(value)}">
                <span>${this.esc(label)}</span>${count ? `<span class="media-chip-count">${count}</span>` : ''}
            </button>`;
        box.innerHTML = '<span class="media-roots-label">平台：</span>'
            + chip('', '全部', total, this._platform === '')
            + items.map((p) => chip(p.platform, p.label || p.platform, p.count, this._platform === p.platform)).join('');
        box.querySelectorAll('[data-platform]').forEach((button) => {
            button.addEventListener('click', () => {
                this._platform = button.dataset.platform || '';
                this._account = '';
                const select = document.getElementById('ms-account');
                if (select) select.value = '';
                this.renderPlatforms();
                this.renderAccounts();
                this.search(true);
            });
        });
    },

    renderAccounts() {
        const select = document.getElementById('ms-account');
        if (!select) return;
        const rows = (this._facets.accounts || []).filter((row) => !this._platform || row.platform === this._platform);
        const options = ['<option value="">全部账号</option>'].concat(rows.map((row) => {
            const label = `${row.account}（${row.count}）`;
            return `<option value="${this.esc(row.account)}">${this.esc(label)}</option>`;
        }));
        select.innerHTML = options.join('');
        if (this._account) select.value = this._account;
    },

    // ── 媒体根与统计 ──────────────────────────────────
    async loadRoots() {
        const select = document.getElementById('ms-root');
        try {
            const data = await API.media.roots();
            this._roots = (data && data.items) || [];
        } catch (err) {
            this._roots = [];
            if (select) select.innerHTML = `<option value="">媒体服务不可用（${err.status || '网络'}）</option>`;
            return;
        }
        if (!select) return;
        if (!this._roots.length) {
            select.innerHTML = '<option value="">没有配置媒体根</option>';
            return;
        }
        select.innerHTML = this._roots.map((r) => {
            const state = r.reachable === true ? '在线' : (r.reachable === false ? '离线' : '未知');
            return `<option value="${this.esc(r.id)}">${this.esc(r.label || r.id)}（${state}）</option>`;
        }).join('');
        const preferred = this._roots.find((r) => r.id === 'articles-full') || this._roots[0];
        select.value = preferred.id;
        this._root = preferred.id;
        select.addEventListener('change', () => { this._root = select.value; });
    },

    async loadStats() {
        const el = document.getElementById('ms-stats');
        if (!el) return;
        try {
            const data = await API.media.contentsFacets();
            const platforms = (data && data.platforms) || [];
            const parts = platforms.map((p) => `${p.label || p.platform} ${p.count}`);
            el.textContent = `已索引 ${data.total || 0} 条内容${parts.length ? '：' + parts.join(' · ') : ''}`;
        } catch (err) {
            el.textContent = '索引统计不可用';
        }
    },

    // ── 检索 ─────────────────────────────────────────
    async search(reset) {
        if (reset) this._offset = 0;
        this._query = (document.getElementById('ms-q')?.value || '').trim();
        const box = document.getElementById('ms-results');
        if (box) box.innerHTML = `<div class="loading-screen"><div class="spinner"></div><p>检索中…</p></div>`;
        try {
            const data = await API.media.contentsSearch({
                q: this._query,
                platform: this._platform,
                account: this._account,
                limit: this._limit,
                offset: this._offset,
            });
            this._items = (data && data.items) || [];
            this._total = (data && data.total) || 0;
            this.renderResults();
            this.renderPager();
        } catch (err) {
            this._items = [];
            this._total = 0;
            this.renderResults(err);
            this.renderPager();
        }
    },

    platformIcon(platform) {
        return { wechat: '📄', channels: '🎬', douyin: '🎵', kuaishou: '⚡', bilibili: '📺', xhs: '📕' }[platform] || '📦';
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
                    <h3 class="empty-state-title">没有匹配的内容</h3>
                    <p class="empty-state-desc">换个说法，或先对来源目录点一次「扫描选中来源」。留给空输入可以直接浏览最近入库。</p>
                </div>`;
            return;
        }
        const esc = (v) => this.esc(v);
        box.innerHTML = `<div class="content-grid">${this._items.map((item, i) => {
            const cover = item.cover
                ? `<img src="${esc('/media' + item.cover.thumb)}" loading="lazy" alt=""
                        onerror="this.closest('.content-cover').classList.add('is-broken')">`
                : `<span class="content-cover-fallback is-${esc(item.platform)}">${this.platformIcon(item.platform)}</span>`;
            const date = (item.published_at || item.indexed_at || '').slice(0, 10);
            const snippet = item.snippet || item.digest || '';
            return `<button type="button" class="content-card" data-index="${i}">
                        <span class="content-cover">
                            ${cover}
                            <span class="media-badge">${esc(item.platform_label || item.platform)}</span>
                        </span>
                        <span class="content-meta">
                            <span class="content-title">${esc(item.title)}</span>
                            <span class="content-sub">${esc(item.account || '未知账号')}${date ? ' · ' + esc(date) : ''}${item.media_count ? ' · ' + item.media_count + ' 张图' : ''}</span>
                            ${snippet ? `<span class="content-snippet">${esc(snippet)}</span>` : ''}
                        </span>
                    </button>`;
        }).join('')}</div>`;
        box.querySelectorAll('.content-card').forEach((card) => {
            card.addEventListener('click', () => this.openDetail(Number(card.dataset.index)));
        });
    },

    renderPager() {
        const pager = document.getElementById('ms-pager');
        if (!pager) return;
        const shown = this._items.length;
        if (!shown && !this._offset) {
            pager.hidden = true;
            pager.innerHTML = '';
            return;
        }
        pager.hidden = false;
        const start = this._offset + 1;
        const end = this._offset + shown;
        pager.innerHTML = `
            <button class="btn btn-secondary btn-sm" id="ms-prev" ${this._offset === 0 ? 'disabled' : ''}>← 上一页</button>
            <span class="form-hint">第 ${start}–${end} 条 / 共 ${this._total} 条</span>
            <button class="btn btn-secondary btn-sm" id="ms-next" ${end >= this._total ? 'disabled' : ''}>下一页 →</button>`;
        const prev = document.getElementById('ms-prev');
        if (prev) prev.addEventListener('click', () => {
            this._offset = Math.max(0, this._offset - this._limit);
            this.search(false);
        });
        const next = document.getElementById('ms-next');
        if (next) next.addEventListener('click', () => {
            this._offset += this._limit;
            this.search(false);
        });
    },

    // ── 详情 ─────────────────────────────────────────
    async openDetail(index) {
        const item = this._items[index];
        if (!item) return;
        this._detail = item.content_id;
        let detail = item;
        try {
            detail = await API.media.contentsDetail(item.content_id);
        } catch (err) {
            // 索引刚被清理时详情 404，用列表项兜底，至少不把用户卡在黑屏。
        }
        this._detail = detail;
        const esc = (v) => this.esc(v);
        const images = (detail.images || []).slice(0, 60);
        const body = detail.body ? `<div class="content-detail-body">${esc(detail.body)}</div>` : '';
        const meta = [
            detail.platform_label || detail.platform,
            detail.account,
            (detail.published_at || '').slice(0, 10),
        ].filter(Boolean).map(esc).join(' · ');
        Modal.open({
            title: `<span id="ms-detail-title">${esc(detail.title)}</span>`,
            content: `
                <div class="content-detail" id="ms-detail-body">
                    <div class="media-lightbox-meta">
                        <div>${meta}</div>
                        <div class="media-path">${esc(detail.root)} / ${esc(detail.relpath)}</div>
                        ${detail.url ? `<div><a href="${esc(detail.url)}" target="_blank" rel="noopener">打开原文</a></div>` : ''}
                    </div>
                    ${body}
                    ${images.length ? `<div class="content-assets">${images.map((image) => `
                        <a href="${esc('/media' + image.file)}" target="_blank" rel="noopener" class="content-asset">
                            <img src="${esc('/media' + image.thumb)}" loading="lazy" alt="">
                        </a>`).join('')}</div>` : ''}
                </div>`,
            footer: `<button class="btn btn-secondary" onclick="MediaSearchPage.closeDetail()">关闭</button>`,
            onClose: () => { this._detail = null; },
        });
    },

    closeDetail() {
        this._detail = null;
        if (typeof Modal !== 'undefined' && Modal.close) Modal.close();
    },

    // ── 扫描 ─────────────────────────────────────────
    async triggerScan() {
        const root = this._root || (document.getElementById('ms-root')?.value || '');
        if (!root) {
            Toast.error('没有可扫描的来源');
            return;
        }
        const state = document.getElementById('ms-scan-state');
        if (state) state.textContent = '正在提交扫描…';
        try {
            const job = await API.media.scan(root, false);
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
                    state.textContent = `扫描中 ${job.scanned || 0} 个文件${job.current ? '：' + job.current : ''}`;
                }
                if (['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
                    clearInterval(this._scanTimer);
                    this._scanTimer = null;
                    const added = job.contents ? (job.contents.new || 0) : 0;
                    const updated = job.contents ? (job.contents.updated || 0) : 0;
                    if (state) {
                        state.textContent = job.status === 'completed'
                            ? `扫描完成：图片新增 ${job.indexed_new || 0}，内容新增 ${added}、更新 ${updated}`
                            : `扫描结束（${job.status}）`;
                    }
                    await this.loadFacets();
                    this.loadStats();
                    this.search(false);
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

    detachKeys() {
        if (this._keyHandler) {
            document.removeEventListener('keydown', this._keyHandler);
            this._keyHandler = null;
        }
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
