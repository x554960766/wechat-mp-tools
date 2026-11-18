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
    _nsfwOnly: false,
    _images: [],
    _viewerCtrl: null,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">素材检索</h2>
                <p class="page-description">按标题、正文和账号检索已下载的内容：公众号文章、视频号/抖音/快手/B站视频、小红书笔记。留空直接看最近入库。</p>
            </div>

            <div class="card" id="ms-search-card" style="margin-bottom: var(--spacing-lg);">
                <div class="media-search-bar">
                    <input type="text" class="form-input" id="ms-q" autocomplete="off"
                           placeholder="例如：手机壁纸 / 翰墨修心 / 咖啡 手写">
                    <button class="btn btn-primary" id="ms-go">检索</button>
                </div>

                <div class="media-search-bar">
                    <input type="file" class="form-input" id="ms-image" accept="image/*,.heic,.heif">
                    <button class="btn btn-primary" id="ms-image-go">以图搜图</button>
                    <button class="btn btn-secondary btn-sm" id="ms-image-clear" type="button">清空</button>
                    <span class="form-hint">也可把图片拖进本页</span>
                </div>

                <div class="ms-image-preview" id="ms-image-preview" hidden>
                    <img class="ms-image-preview-img" id="ms-image-preview-img" alt=""
                         onerror="this.style.visibility='hidden'">
                    <div class="ms-image-preview-meta">
                        <span class="ms-image-preview-name" id="ms-image-preview-name"></span>
                        <span class="ms-image-preview-sub" id="ms-image-preview-sub"></span>
                    </div>
                    <span class="ms-image-preview-tag" id="ms-image-preview-tag"></span>
                </div>

                <div class="media-search-bar">
                    <input type="url" class="form-input" id="ms-image-url"
                           placeholder="或粘贴图片网址（http/https）">
                    <button class="btn btn-secondary" id="ms-image-url-go">搜网图</button>
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
                    <span class="form-hint" id="ms-filter-note"></span>
                    <span class="media-toolbar-right">
                        <label class="media-toggle" title="只显示含疑似 NSFW 图片的条目；标记不改变检索排序">
                            <input type="checkbox" id="ms-nsfw-only"> 只看含疑似 NSFW
                        </label>
                        <button class="btn btn-secondary btn-sm" id="ms-nsfw-review"
                                title="列出仍判疑似敏感的图片；打开大图后可确认敏感或标为误报">待复核</button>
                        <select class="form-input ms-tag-filter" id="ms-tag-filter"
                                title="按标签浏览图片；标签挂在内容摘要上，同内容副本共享">
                            <option value="">按标签浏览</option>
                        </select>
                        <button class="btn btn-secondary btn-sm" id="ms-tag-rename"
                                title="整体改名选中的标签；新名字已存在时两边合并成一个标签" hidden>改名</button>
                        <span class="form-hint" id="ms-scan-state"></span>
                        <button class="btn btn-secondary btn-sm" id="ms-scan">扫描选中来源</button>
                    </span>
                </div>
            </div>

            <div id="ms-video-hits" class="media-video-hits" hidden></div>

            <div id="ms-image-hits" class="media-results" hidden></div>
            <p id="ms-image-status" class="form-hint" role="status"></p>

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
        this._nsfwOnly = false;
        this._images = [];
        this._flaggedMode = false;

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
        const nsfwOnly = document.getElementById('ms-nsfw-only');
        if (nsfwOnly) {
            nsfwOnly.checked = this._nsfwOnly;
            nsfwOnly.addEventListener('change', () => {
                this._nsfwOnly = nsfwOnly.checked;
                // 纯前端筛选当前页：不重新请求，避免标记开关改变检索结果顺序
                this.renderResults();
            });
        }
        const nsfwReview = document.getElementById('ms-nsfw-review');
        if (nsfwReview) nsfwReview.addEventListener('click', () => this.loadNsfwFlagged());
        const tagFilter = document.getElementById('ms-tag-filter');
        if (tagFilter) tagFilter.addEventListener('change', () => {
            this.syncTagRenameButton();
            if (tagFilter.value) this.loadImagesByTag(tagFilter.value);
            else this.search(true); // 清空选择回到常规检索结果
        });
        const tagRename = document.getElementById('ms-tag-rename');
        if (tagRename) tagRename.addEventListener('click', () => this.renameSelectedTag());
        const account = document.getElementById('ms-account');
        if (account) {
            account.addEventListener('change', () => {
                this._account = account.value;
                this.search(true);
            });
        }
        const imageGo = document.getElementById('ms-image-go');
        if (imageGo) imageGo.addEventListener('click', () => this.imageSearch());
        const imageInput = document.getElementById('ms-image');
        if (imageInput) {
            imageInput.addEventListener('change', () => {
                if (imageInput.files && imageInput.files[0]) this.imageSearch();
            });
        }
        const imageClear = document.getElementById('ms-image-clear');
        if (imageClear) imageClear.addEventListener('click', () => this.clearImageSource());
        const imageUrlGo = document.getElementById('ms-image-url-go');
        if (imageUrlGo) imageUrlGo.addEventListener('click', () => this.imageSearch());
        this.bindImageDrop();
        await this.loadFacets();
        await this.loadRoots();
        this.loadTagFilter();
        this.loadStats();
        await this.search(true);
    },

    destroy() {
        clearInterval(this._scanTimer);
        this._scanTimer = null;
        this.closeViewer();
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
            button.addEventListener('click', async () => {
                this._platform = button.dataset.platform || '';
                this._account = '';
                const select = document.getElementById('ms-account');
                if (select) select.value = '';
                // 平台切换后重新拉取分组计数：索引可能刚被下载后的扫描更新过，
                // 只重渲染本地缓存会让计数停留在旧值。
                await this.loadFacets();
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
            this.searchVideoHits();
        } catch (err) {
            this._items = [];
            this._total = 0;
            this.renderResults(err);
            this.renderPager();
        }
    },

    /**
     * 视频画面命中：把查询文本同时送进图片/视频帧向量（媒体服务一次编码两用），
     * 结果按视频归并，每个视频给出代表帧与命中时间点。
     *
     * 与内容检索互相独立：这里失败只是没有画面命中，不影响上面已经渲染的条目列表。
     */
    async searchVideoHits() {
        const box = document.getElementById('ms-video-hits');
        if (!box) return;
        if (!this._query) {
            box.hidden = true;
            box.innerHTML = '';
            return;
        }
        try {
            const data = await API.media.searchText({ q: this._query, k: 8, videos: true }, { showError: false });
            this.renderVideoHits((data && data.videos) || []);
        } catch (err) {
            box.hidden = true;
            box.innerHTML = '';
        }
    },

    renderVideoHits(groups) {
        const box = document.getElementById('ms-video-hits');
        if (!box) return;
        if (!groups.length) {
            box.hidden = true;
            box.innerHTML = '';
            return;
        }
        const esc = (v) => this.esc(v);
        const cards = groups.map((group) => {
            const poster = group.hits && group.hits[0] ? `/media${group.hits[0].thumb}` : '';
            const chips = (group.hits || []).map((hit) => {
                const url = `/media${group.file}#t=${Math.max(0, Math.round(hit.ts))}`;
                return `<a class="video-ts" href="${esc(url)}" target="_blank" rel="noopener">${esc(this.fmtTs(hit.ts))}</a>`;
            }).join('');
            const duration = group.duration ? `${Math.round(group.duration)} 秒` : '未知时长';
            return `
                <article class="video-hit">
                    ${poster ? `<img src="${esc(poster)}" loading="lazy" alt="">` : ''}
                    <div class="video-hit-body">
                        <p class="video-hit-title" title="${esc(group.relpath)}">${esc(group.relpath)}</p>
                        <p class="form-hint">${esc(group.source_label || group.root)} · ${esc(duration)} · 画面相似度 ${Number(group.score).toFixed(2)}</p>
                        <div class="video-hit-times">${chips || '<span class="form-hint">暂无命中帧</span>'}</div>
                    </div>
                </article>`;
        }).join('');
        // 内容条目与画面命中是两套索引：仅画面命中时把空态说清楚，别让人以为检索失败
        const empty = document.querySelector('#ms-results .empty-state-title');
        if (empty && empty.textContent.includes('没有匹配的内容')) empty.textContent = '没有匹配的内容条目';
        box.hidden = false;
        box.innerHTML = `
            <div class="card">
                <div class="media-toolbar">
                    <span class="form-hint">视频画面命中 ${groups.length} 条（点时间点从该处播放）</span>
                </div>
                <div class="video-hit-list">${cards}</div>
            </div>`;
    },

    // ── 以图搜图 ──────────────────────────────────────
    /**
     * 上传一张图，在当前媒体根里找语义最相近的图片。
     *
     * 请求体是 FormData：接口收 multipart，不能走 JSON 序列化（见 API.request 的
     * FormData 分支）。roots 用当前「媒体来源」选择，未指定时由后端按全部根检索。
     * 失败只清空图片结果并提示，不影响同页的内容检索。
     */
    async imageSearch(file, url) {
        const hasFile = file !== undefined && file !== null;
        const hasUrl = url !== undefined && url !== null;
        const input = document.getElementById('ms-image');
        const chosen = hasFile ? file : (hasUrl ? null : (input && input.files && input.files[0]));
        const imageUrl = hasUrl ? url : '';
        if (!chosen && !imageUrl) {
            this.imageStatus('请先选择一张图片，或粘贴图片网址');
            return;
        }
        if (chosen && !/^image\//.test(chosen.type) && !/\.(heic|heif)$/i.test(chosen.name)) {
            this.imageStatus('只支持图片文件');
            return;
        }
        if (!chosen && !/^https?:\/\//i.test(imageUrl)) {
            this.imageStatus('图片网址需以 http:// 或 https:// 开头');
            return;
        }
        // 回显查询图来源：选文件、拖入、贴网址三种入口都能看到当前实际用的是哪个
        this.renderImagePreview(chosen, imageUrl);
        const hits = document.getElementById('ms-image-hits');
        if (hits) {
            hits.hidden = false;
            hits.innerHTML = `<div class="loading-screen"><div class="spinner"></div><p>正在检索相似图片…</p></div>`;
        }
        this.imageStatus('正在检索…');
        try {
            const fd = new FormData();
            if (chosen) fd.append('file', chosen);
            else fd.append('url', imageUrl);
            if (this._root) fd.append('roots', this._root);
            fd.append('k', 24);
            fd.append('review', String(this._nsfwOnly));
            const data = await API.media.searchImage(fd, { showError: false });
            this._images = (data && data.items) || [];
            this._flaggedMode = false;
            this.renderImageResults();
            this.renderVideoHits((data && data.videos) || []);
            this.imageStatus(this._images.length
                ? `以图搜图命中 ${this._images.length} 张相似图片`
                : '没有匹配的相似图片');
        } catch (err) {
            this._images = [];
            this.renderImageResults(err);
            this.imageStatus(`以图搜图失败：${err.message}`);
        }
    },

    /**
     * 回显当前查询图。本地文件显示缩略图、文件名与体积；网图显示网址与来源标签，
     * 并尝试让浏览器直接加载缩略图（部分站点防盗链会失败，失败时图自动隐藏，网址仍在）。
     */
    renderImagePreview(file, url) {
        const box = document.getElementById('ms-image-preview');
        if (!box) return;
        const img = document.getElementById('ms-image-preview-img');
        const name = document.getElementById('ms-image-preview-name');
        const sub = document.getElementById('ms-image-preview-sub');
        const tag = document.getElementById('ms-image-preview-tag');
        if (file) {
            if (img) {
                img.style.visibility = '';
                img.src = URL.createObjectURL(file);
                img.alt = file.name;
            }
            if (name) name.textContent = file.name;
            if (sub) sub.textContent = this.fmtBytes(file.size);
            if (tag) tag.textContent = '本地文件';
        } else if (url) {
            if (img) {
                img.style.visibility = '';
                img.src = url;
                img.alt = url;
            }
            if (name) name.textContent = url;
            if (sub) sub.textContent = '网图（后端下载后检索）';
            if (tag) tag.textContent = '网址';
        } else {
            // 清空必须把缩略图 src 一并抹掉：只设 hidden 不够——.ms-image-preview 的
            // display:flex 会盖掉 hidden 的默认 display:none，旧图会一直留在框里。
            if (img) {
                const stale = img.src;
                img.src = '';
                if (stale.startsWith('blob:')) URL.revokeObjectURL(stale);
            }
            if (name) name.textContent = '';
            if (sub) sub.textContent = '';
            if (tag) tag.textContent = '';
            box.hidden = true;
            return;
        }
        box.hidden = false;
    },

    /** 清空查询图来源：文件框、网址框、回显与图片结果一起复位，避免残留旧图误导。 */
    clearImageSource() {
        const input = document.getElementById('ms-image');
        if (input) input.value = '';
        const urlInput = document.getElementById('ms-image-url');
        if (urlInput) urlInput.value = '';
        this.renderImagePreview(null, '');
        const hits = document.getElementById('ms-image-hits');
        if (hits) { hits.hidden = true; hits.innerHTML = ''; }
        this._images = [];
        this.imageStatus('');
    },

    fmtBytes(bytes) {
        const n = Number(bytes) || 0;
        if (!n) return '0 B';
        const units = ['B', 'KB', 'MB', 'GB'];
        let value = n;
        let unit = 0;
        while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
        return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
    },

    imageStatus(msg) {
        const el = document.getElementById('ms-image-status');
        if (!el) return;
        el.textContent = msg || '';
    },

    nsfwOf(image) {
        if (!image) return null;
        if (image.nsfw && image.nsfw.status === 'ok') return image.nsfw;
        if (image.review && image.review.status === 'ok') return image.review;
        return null;
    },

    /** 集中复核：列出仍判疑似敏感、且未被人工复核过的图片（second 级结论会使其出列）。 */
    async loadNsfwFlagged() {
        const status = document.getElementById('ms-image-status');
        if (status) status.textContent = '正在读取待复核图片…';
        try {
            const data = await API.media.nsfwFlagged({ include_reviewed: false, limit: 60 });
            const items = (data.items || []).map((row) => ({
                ...row,
                nsfw: { label: row.label, score: row.score, stage: row.stage, status: 'ok',
                        nsfw_type: row.nsfw_type, reviewed_by: row.reviewed_by,
                        reviewed_at: row.reviewed_at },
            }));
            this._images = items;
            this._flaggedMode = true;
            this.renderNsfwFlagged(data.total != null ? data.total : items.length);
        } catch (err) {
            if (status) status.textContent = '';
            Toast.error('读取待复核图片失败：' + (err && err.message ? err.message : '请求失败'));
        }
    },

    renderNsfwFlagged(total) {
        const box = document.getElementById('ms-image-hits');
        if (!box) return;
        const status = document.getElementById('ms-image-status');
        if (status) status.textContent = total ? `待复核 ${total} 张（按分数从高到低，打开大图可复核）` : '';
        box.hidden = false;
        if (!this._images.length) {
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🛡️</div>
                    <h3 class="empty-state-title">没有待复核的图片</h3>
                    <p class="empty-state-desc">当前索引里没有仍判疑似敏感、且未被人工复核过的图片。</p>
                </div>`;
            return;
        }
        this.renderImageCards(box, this._images, (item) => {
            const score = item.nsfw && item.nsfw.score != null ? Number(item.nsfw.score).toFixed(2) : '—';
            return { cls: 'is-warn', text: `疑似 ${score}` };
        });
    },

    /** 一批图片卡片（待复核 / 按标签浏览共用）：缩略图 + 角标 + 路径，点击打开查看器。 */
    renderImageCards(box, items, badgeFor) {
        const esc = (v) => this.esc(v);
        box.innerHTML = `<div class="content-grid">${items.map((item, i) => {
            const badge = badgeFor ? badgeFor(item) : null;
            return `<button type="button" class="content-card ms-image-card" data-index="${i}">
                        <span class="content-cover">
                            <img src="${esc('/media' + (item.thumb || ''))}" loading="lazy" alt=""
                                 onerror="this.closest('.content-cover').classList.add('is-broken')">
                            ${badge ? `<span class="media-badge ${esc(badge.cls)}">${esc(badge.text)}</span>` : ''}
                        </span>
                        <span class="content-meta">
                            <span class="content-title">${esc(item.relpath || '')}</span>
                            <span class="content-sub">${esc(item.root || '')}</span>
                        </span>
                    </button>`;
        }).join('')}</div>`;
        box.querySelectorAll('.ms-image-card').forEach((card) => {
            card.addEventListener('click', () => this.openImage(Number(card.dataset.index)));
        });
    },

    /** 标签下拉：字典来自 /api/tags（文件行 + 作品行的使用次数）。读不到不影响检索。 */
    async loadTagFilter() {
        const select = document.getElementById('ms-tag-filter');
        if (!select) return;
        const esc = (v) => this.esc(v);
        try {
            const data = await API.media.tags({ showError: false });
            const rows = data && data.items ? data.items : [];
            const current = select.value;
            select.innerHTML = '<option value="">按标签浏览</option>' + rows.map((row) => (
                `<option value="${esc(row.tag)}">${esc(row.tag)}（${esc(row.count)}）</option>`
            )).join('');
            // 重新读取后保留用户已选的标签，避免刚打完标签下拉就跳回「按标签浏览」
            if (current && rows.some((row) => row.tag === current)) select.value = current;
            this.syncTagRenameButton();
        } catch (err) {
            select.innerHTML = '<option value="">按标签浏览</option>';
        }
    },

    /** 选中标签时显示「改名」按钮，清空选择时隐藏。 */
    syncTagRenameButton() {
        const btn = document.getElementById('ms-tag-rename');
        if (!btn) return;
        const select = document.getElementById('ms-tag-filter');
        btn.hidden = !(select && select.value);
    },

    /** 把当前选中的标签整体改名；新名已存在时后端合并为同一标签。 */
    renameSelectedTag() {
        const select = document.getElementById('ms-tag-filter');
        const oldTag = select ? select.value : '';
        if (!oldTag) return;
        Modal.prompt('标签改名', `把「${oldTag}」改名为：`, oldTag, async (value) => {
            const newTag = (value || '').trim();
            if (!newTag || newTag === oldTag) return; // 没改名字就不发请求
            try {
                const result = await API.media.renameTag(oldTag, newTag, { showError: false });
                await this.loadTagFilter();
                select.value = newTag;
                this.syncTagRenameButton();
                const merged = result && result.merged ? '，并合并了同名标签' : '';
                Toast.success(`已改名：${oldTag} → ${newTag}（图片 ${result.images || 0} 张、作品 ${result.contents || 0} 个${merged}）`);
                this.loadImagesByTag(newTag);
            } catch (err) {
                Toast.error('标签改名失败：' + (err && err.message ? err.message : '请求失败'));
            }
        }, { placeholder: '新标签名' });
    },

    /** 按标签列图片：行结构与搜索结果同构，可直接喂给同一个查看器。 */
    async loadImagesByTag(tag) {
        const status = document.getElementById('ms-image-status');
        if (status) status.textContent = `正在读取「${tag}」的图片…`;
        try {
            const data = await API.media.imagesByTag(tag, { limit: 60 });
            this._images = data && data.items ? data.items : [];
            this._flaggedMode = false;
            this.renderTagResults(tag, data || {});
        } catch (err) {
            if (status) status.textContent = '';
            Toast.error('按标签读取图片失败：' + (err && err.message ? err.message : '请求失败'));
        }
    },

    renderTagResults(tag, data) {
        const box = document.getElementById('ms-image-hits');
        if (!box) return;
        const status = document.getElementById('ms-image-status');
        const items = this._images;
        box.hidden = false;
        if (status) {
            status.textContent = items.length
                ? `标签「${tag}」共 ${items.length} 张${data.has_more ? '（仅显示前 ' + items.length + ' 张）' : ''}`
                : '';
        }
        if (!items.length) {
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🏷️</div>
                    <h3 class="empty-state-title">这个标签下还没有图片</h3>
                    <p class="empty-state-desc">打开任意图片的查看器输入标签，即可把图片归到这里。</p>
                </div>`;
            return;
        }
        this.renderImageCards(box, items, (item) => {
            const nsfw = this.nsfwOf(item);
            if (!nsfw || nsfw.label !== 'nsfw') return null;
            const score = nsfw.score != null ? Number(nsfw.score).toFixed(2) : '—';
            return { cls: 'is-warn', text: `疑似 ${score}` };
        });
    },

    bindImageDrop() {
        const card = document.getElementById('ms-search-card');
        if (!card) return;
        card.addEventListener('dragover', (e) => {
            e.preventDefault();
            card.classList.add('dragover');
        });
        card.addEventListener('dragleave', (e) => {
            if (e.target === card) card.classList.remove('dragover');
        });
        card.addEventListener('drop', async (e) => {
            e.preventDefault();
            card.classList.remove('dragover');
            const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
            if (!file) return;
            await this.imageSearch(file);
        });
    },

    renderImageResults(err) {
        const box = document.getElementById('ms-image-hits');
        if (!box) return;
        if (err) {
            const code = err.status ? `HTTP ${err.status}` : '请求失败';
            box.hidden = false;
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">⚠️</div>
                    <h3 class="empty-state-title">以图搜图失败（${code}）</h3>
                    <p class="empty-state-desc">${this.esc(err.message || '')}</p>
                </div>`;
            return;
        }
        if (!this._images.length) {
            box.hidden = false;
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🫧</div>
                    <h3 class="empty-state-title">没有匹配的相似图片</h3>
                    <p class="empty-state-desc">换一张特征更独特的图片，或先对来源目录点一次「扫描选中来源」补齐索引。</p>
                </div>`;
            return;
        }
        const esc = (v) => this.esc(v);
        box.hidden = false;
        box.innerHTML = `<div class="content-grid">${this._images.map((item, i) => {
            const thumb = '/media' + (item.thumb || '');
            const title = (item.article && item.article.title) || item.relpath || '图片';
            const sub = `相似度 ${Number(item.score).toFixed(2)} · ${item.shot_at || '时间未知'}`;
            const risky = item.review && item.review.status === 'ok' && item.review.label === 'nsfw'
                ? `疑似 NSFW ${Number(item.review.score).toFixed(2)}`
                : (item.nsfw && item.nsfw.status === 'ok' && item.nsfw.label === 'nsfw'
                    ? `疑似 NSFW ${Number(item.nsfw.score).toFixed(2)}` : '');
            return `<button type="button" class="content-card ms-image-card" data-index="${i}">
                        <span class="content-cover">
                            <img src="${esc(thumb)}" loading="lazy" alt=""
                                 onerror="this.closest('.content-cover').classList.add('is-broken')">
                            <span class="media-badge">${esc(item.source_label || item.root || '')}</span>
                            ${risky ? `<span class="media-badge is-warn">${esc(risky)}</span>` : ''}
                        </span>
                        <span class="content-meta">
                            <span class="content-title">${esc(title)}</span>
                            <span class="content-sub">${esc(sub)}</span>
                        </span>
                    </button>`;
        }).join('')}</div>`;
        box.querySelectorAll('.ms-image-card').forEach((card) => {
            card.addEventListener('click', () => this.openImage(Number(card.dataset.index)));
        });
    },

    openImage(index) {
        const image = this._images[index];
        if (!image) return;
        // _images 里的字段（file / relpath / nsfw）与详情图同构，可直接喂给查看器
        this.openViewer(this._images, index);
    },

    fmtTs(seconds) {
        const total = Math.max(0, Math.round(seconds || 0));
        return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
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
            this.renderFilterNote(0);
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🫧</div>
                    <h3 class="empty-state-title">没有匹配的内容</h3>
                    <p class="empty-state-desc">换个说法，或先对来源目录点一次「扫描选中来源」。留给空输入可以直接浏览最近入库。</p>
                </div>`;
            return;
        }
        // 筛选只作用于当前页：切换开关不重新请求，检索顺序与条数都不受影响
        const shown = this._items
            .map((item, index) => ({ item, index }))
            .filter((row) => !this._nsfwOnly || this.isRisky(row.item));
        this.renderFilterNote(shown.length);
        if (!shown.length) {
            box.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🛡️</div>
                    <h3 class="empty-state-title">当前筛选下没有内容</h3>
                    <p class="empty-state-desc">本页 ${this._items.length} 条都不含疑似 NSFW 图片；取消勾选即可看全部。</p>
                </div>`;
            return;
        }
        const esc = (v) => this.esc(v);
        box.innerHTML = `<div class="content-grid">${shown.map(({ item, index: i }) => {
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
                            ${this.isRisky(item) ? `<span class="media-badge is-warn">疑似 NSFW ${Number(item.nsfw.flagged)}</span>` : ''}
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

    /**
     * 条目是否含疑似 NSFW 图片。后端只做聚合（该条目已入库图片里初筛命中的张数），
     * 条目本身没有「是否 NSFW」属性，所以这里只看 flagged 计数。
     */
    isRisky(item) {
        return !!(item && item.nsfw && Number(item.nsfw.flagged) > 0);
    },

    renderFilterNote(shownCount) {
        const el = document.getElementById('ms-filter-note');
        if (!el) return;
        if (!this._nsfwOnly) {
            el.textContent = '';
            return;
        }
        const hidden = Math.max(0, this._items.length - shownCount);
        el.textContent = hidden ? `已按筛选隐藏 ${hidden} 条` : '筛选已开启';
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
                    ${images.length ? `<div class="content-assets">${images.map((image, i) => `
                        <button type="button" class="content-asset" data-image="${i}" title="点击查看大图 / 全景">
                            <img src="${esc('/media' + image.thumb)}" loading="lazy" alt="">
                            ${image.nsfw && image.nsfw.label === 'nsfw'
                                ? `<span class="media-badge is-warn">疑似 ${Number(image.nsfw.score).toFixed(2)}</span>` : ''}
                        </button>`).join('')}</div>` : ''}
                </div>`,
            footer: `<button class="btn btn-secondary" onclick="MediaSearchPage.closeDetail()">关闭</button>`,
            onClose: () => { this._detail = null; this.closeViewer(); },
        });
        const detailBody = document.getElementById('ms-detail-body');
        if (detailBody) {
            detailBody.querySelectorAll('[data-image]').forEach((button) => {
                button.addEventListener('click', () => this.openViewer(images, Number(button.dataset.image)));
            });
        }
    },

    closeDetail() {
        this._detail = null;
        this.closeViewer();
        if (typeof Modal !== 'undefined' && Modal.close) Modal.close();
    },

    // ── 图片查看器（含 VR 全景）────────────────────────────
    /**
     * 查看面板由共享的 MediaViewer 承担（DOM 前缀 ms，样式沿用 .ms-viewer 系列）；
     * 本页只注入标签与 NSFW 复核所需的数据行为，全景口径与媒体服务自带页一致。
     */
    openViewer(images, index) {
        if (!this._viewerCtrl) this._viewerCtrl = new MediaViewer('ms');
        this._viewerCtrl.open(images, index, {
            getNsfw: (image) => this.nsfwOf(image),
            loadTags: (image) => API.media.imageTags(image.root, image.relpath, { showError: false })
                .then((data) => data && data.tags),
            saveTag: (image, tag, mode) => API.media.tagImage({
                root: image.root, relpath: image.relpath, tag, mode,
            }).then((data) => data && data.tags),
            review: async (image, label) => {
                const data = await API.media.nsfwReview({
                    root: image.root, relpath: image.relpath, label, reviewed_by: '本机',
                });
                image.nsfw = data.nsfw;
                image.review = null;
                if (this._flaggedMode) await this.loadNsfwFlagged(); // 误报出列后列表同步
            },
            onTagsChanged: () => this.loadTagFilter(), // 新标签立刻出现在「按标签浏览」下拉里
        });
    },

    viewerShow(index) {
        if (this._viewerCtrl) this._viewerCtrl.show(index);
    },

    viewerStep(delta) {
        if (this._viewerCtrl) this._viewerCtrl.step(delta);
    },

    viewerPanorama() {
        return this._viewerCtrl ? this._viewerCtrl.panorama() : undefined;
    },

    async loadThree() {
        if (!this._viewerCtrl) this._viewerCtrl = new MediaViewer('ms');
        return this._viewerCtrl.loadThree();
    },

    viewerBuildPano(THREE, image, viewer) {
        if (this._viewerCtrl) this._viewerCtrl.buildPano(THREE, image, viewer);
    },

    viewerApplyPanoMode() {
        if (this._viewerCtrl) this._viewerCtrl.applyPanoMode();
    },

    viewerExitPano() {
        if (this._viewerCtrl) this._viewerCtrl.exitPano();
    },

    disposePano(pano) {
        if (this._viewerCtrl) this._viewerCtrl.disposePano(pano);
    },

    viewerStatus(msg) {
        if (this._viewerCtrl) this._viewerCtrl.status(msg);
    },

    closeViewer() {
        if (this._viewerCtrl) this._viewerCtrl.close();
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
