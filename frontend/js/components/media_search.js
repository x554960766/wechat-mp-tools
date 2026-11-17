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
    _viewer: null,
    _viewerKeys: null,
    _three: null,
    _threePromise: null,

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
                    <span class="form-hint" id="ms-filter-note"></span>
                    <span class="media-toolbar-right">
                        <label class="media-toggle" title="只显示含疑似 NSFW 图片的条目；标记不改变检索排序">
                            <input type="checkbox" id="ms-nsfw-only"> 只看含疑似 NSFW
                        </label>
                        <span class="form-hint" id="ms-scan-state"></span>
                        <button class="btn btn-secondary btn-sm" id="ms-scan">扫描选中来源</button>
                    </span>
                </div>
            </div>

            <div id="ms-video-hits" class="media-video-hits" hidden></div>

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
     * 详情里的缩略图点开后的查看面板。这里刻意不复用 Modal：Modal 是单例覆盖层，
     * 打开会整体替换弹窗内容，返回时详情就没了。
     *
     * 全景与媒体服务自带页同一口径：等距柱状图贴到内向球体上（相机在球心），
     * 上下立体图只取上半幅单眼——直接铺满会因共用整幅宽高比而变形。
     */
    openViewer(images, index) {
        this.closeViewer();
        if (!images || !images.length) return;
        const box = document.createElement('div');
        box.className = 'ms-viewer';
        box.innerHTML = `
            <div class="ms-viewer-panel" role="dialog" aria-modal="true" aria-label="图片查看">
                <div class="ms-viewer-bar">
                    <span class="form-hint" id="ms-viewer-count"></span>
                    <span class="ms-viewer-spacer"></span>
                    <button class="btn btn-secondary btn-sm" data-act="prev">上一张</button>
                    <button class="btn btn-secondary btn-sm" data-act="next">下一张</button>
                    <span class="ms-viewer-pano-controls" id="ms-pano-controls" hidden>
                        <label class="form-hint">全景模式
                            <select class="form-input" id="ms-pano-mode">
                                <option value="mono">单目</option>
                                <option value="stereo">上下立体（取上半幅）</option>
                            </select>
                        </label>
                        <button class="btn btn-secondary btn-sm" data-act="pano-exit">退出全景</button>
                    </span>
                    <button class="btn btn-primary btn-sm" data-act="pano">全景查看</button>
                    <button class="btn btn-secondary btn-sm" data-act="close">关闭</button>
                </div>
                <div class="ms-viewer-stage">
                    <img id="ms-viewer-img" alt="">
                    <div class="ms-viewer-pano" id="ms-viewer-pano" hidden></div>
                </div>
                <p class="form-hint" id="ms-viewer-status" role="status" hidden></p>
            </div>`;
        document.body.appendChild(box);
        this._viewer = { box, images, index: 0, seq: 0, pano: null };
        box.addEventListener('click', (event) => {
            if (event.target === box) {
                this.closeViewer();
                return;
            }
            const act = event.target.closest('[data-act]');
            if (!act) return;
            const name = act.dataset.act;
            if (name === 'close') this.closeViewer();
            else if (name === 'prev') this.viewerStep(-1);
            else if (name === 'next') this.viewerStep(1);
            else if (name === 'pano') this.viewerPanorama();
            else if (name === 'pano-exit') this.viewerExitPano();
        });
        const mode = document.getElementById('ms-pano-mode');
        if (mode) mode.addEventListener('change', () => this.viewerApplyPanoMode());
        this._viewerKeys = (event) => {
            if (!this._viewer) return;
            if (event.key === 'Escape') {
                this.closeViewer();
                return;
            }
            if (this._viewer.pano) return; // 全景下方向键留给场景拖动
            if (event.key === 'ArrowLeft') this.viewerStep(-1);
            if (event.key === 'ArrowRight') this.viewerStep(1);
        };
        document.addEventListener('keydown', this._viewerKeys);
        this.viewerShow(index);
    },

    viewerShow(index) {
        const viewer = this._viewer;
        if (!viewer) return;
        const total = viewer.images.length;
        viewer.index = ((Math.trunc(index) % total) + total) % total;
        const image = viewer.images[viewer.index];
        this.viewerExitPano();
        const img = document.getElementById('ms-viewer-img');
        const count = document.getElementById('ms-viewer-count');
        if (count) count.textContent = `${viewer.index + 1} / ${total} · ${image.relpath || ''}`;
        if (img) {
            img.src = `/media${image.file}`;
            img.alt = image.relpath || '';
            img.hidden = false;
        }
        const risky = image.nsfw && image.nsfw.label === 'nsfw';
        this.viewerStatus(risky ? `初筛 疑似 NSFW（${Number(image.nsfw.score).toFixed(3)}）` : '');
    },

    viewerStep(delta) {
        const viewer = this._viewer;
        if (!viewer) return;
        this.viewerShow(viewer.index + delta);
    },

    async viewerPanorama() {
        const viewer = this._viewer;
        if (!viewer) return;
        const image = viewer.images[viewer.index];
        this.viewerExitPano();
        // 代次必须在第一个 await 之前建立：等待 Three.js 期间退出或切图要能作废本次加载，
        // 否则回来时会往已经关掉的查看器里塞场景。
        const token = ++viewer.seq;
        this.viewerStatus('正在加载全景…');
        let THREE = null;
        try {
            THREE = await this.loadThree();
        } catch (err) {
            if (this._viewer === viewer && token === viewer.seq) {
                this.viewerStatus('Three.js 加载失败：' + (err && err.message ? err.message : '网络不可用'));
            }
            return;
        }
        if (this._viewer !== viewer || token !== viewer.seq) return;
        this.viewerBuildPano(THREE, image, viewer);
    },

    async loadThree() {
        if (this._three) return this._three;
        if (!this._threePromise) {
            // 固定版本，与媒体服务自带页保持一致；只在首次点「全景查看」时才下载
            this._threePromise = import('https://unpkg.com/three@0.160.0/build/three.module.min.js')
                .then((mod) => {
                    this._three = mod;
                    return mod;
                })
                .catch((err) => {
                    this._threePromise = null;
                    throw err;
                });
        }
        return this._threePromise;
    },

    viewerBuildPano(THREE, image, viewer) {
        const stage = document.getElementById('ms-viewer-pano');
        const img = document.getElementById('ms-viewer-img');
        if (!stage || !viewer) return;
        const width = stage.clientWidth || 800;
        const height = stage.clientHeight || 450;
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(width, height, false);
        stage.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(90, width / height, 0.1, 1100);
        const geometry = new THREE.SphereGeometry(500, 64, 40);
        geometry.scale(-1, 1, 1); // 法线朝内：等于把相机放在球心往外看
        const texture = new THREE.TextureLoader().load(`/media${image.file}`, () => {
            if (viewer.pano && viewer.pano.texture === texture) this.viewerStatus('拖动旋转，滚轮缩放');
        }, undefined, () => {
            if (viewer.pano && viewer.pano.texture === texture) this.viewerStatus('全景原图加载失败');
        });
        if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
        const material = new THREE.MeshBasicMaterial({ map: texture });
        const mesh = new THREE.Mesh(geometry, material);
        scene.add(mesh);
        const pano = {
            renderer, scene, camera, geometry, material, texture, mesh,
            yaw: 0, pitch: 0, fov: 90, mode: 'mono', raf: 0,
        };
        viewer.pano = pano;
        stage.hidden = false;
        if (img) img.hidden = true;
        const controls = document.getElementById('ms-pano-controls');
        if (controls) controls.hidden = false;
        this.viewerApplyPanoMode();
        const canvas = renderer.domElement;
        let dragging = false;
        let lastX = 0;
        let lastY = 0;
        const onDown = (event) => {
            dragging = true;
            lastX = event.clientX;
            lastY = event.clientY;
            if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
        };
        const onMove = (event) => {
            if (!dragging) return;
            pano.yaw += (event.clientX - lastX) * 0.0045;
            const limit = Math.PI / 2 - 0.05;
            pano.pitch = Math.max(-limit, Math.min(limit, pano.pitch + (event.clientY - lastY) * 0.0045));
            lastX = event.clientX;
            lastY = event.clientY;
        };
        const onUp = () => { dragging = false; };
        const onWheel = (event) => {
            event.preventDefault();
            pano.fov = Math.max(35, Math.min(100, pano.fov + Math.sign(event.deltaY) * 4));
            camera.fov = pano.fov;
            camera.updateProjectionMatrix();
        };
        canvas.addEventListener('pointerdown', onDown);
        canvas.addEventListener('pointermove', onMove);
        canvas.addEventListener('pointerup', onUp);
        canvas.addEventListener('pointerleave', onUp);
        canvas.addEventListener('wheel', onWheel, { passive: false });
        pano.detach = () => {
            canvas.removeEventListener('pointerdown', onDown);
            canvas.removeEventListener('pointermove', onMove);
            canvas.removeEventListener('pointerup', onUp);
            canvas.removeEventListener('pointerleave', onUp);
            canvas.removeEventListener('wheel', onWheel);
        };
        const resize = () => {
            if (!viewer.pano) return;
            const w = stage.clientWidth || width;
            const h = stage.clientHeight || height;
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        };
        pano.resize = resize;
        window.addEventListener('resize', resize);
        const loop = () => {
            if (!viewer.pano || viewer.pano !== pano) return;
            camera.rotation.order = 'YXZ';
            camera.rotation.y = pano.yaw;
            camera.rotation.x = pano.pitch;
            renderer.render(scene, camera);
            pano.raf = requestAnimationFrame(loop);
        };
        loop();
    },

    viewerApplyPanoMode() {
        const viewer = this._viewer;
        const pano = viewer && viewer.pano;
        if (!pano) return;
        const select = document.getElementById('ms-pano-mode');
        pano.mode = select ? select.value : 'mono';
        if (pano.mode === 'stereo') {
            // 上下立体：v 取 [0.5,1]，即整幅图的上半副眼图
            pano.texture.repeat.y = 0.5;
            pano.texture.offset.y = 0.5;
        } else {
            pano.texture.repeat.y = 1;
            pano.texture.offset.y = 0;
        }
        pano.texture.needsUpdate = true;
    },

    viewerExitPano() {
        const viewer = this._viewer;
        if (!viewer) return;
        const pano = viewer.pano;
        viewer.pano = null;
        viewer.seq += 1; // 作废仍在等待的加载
        this.disposePano(pano);
        const stage = document.getElementById('ms-viewer-pano');
        if (stage) {
            stage.hidden = true;
            stage.innerHTML = '';
        }
        const img = document.getElementById('ms-viewer-img');
        if (img) img.hidden = false;
        const controls = document.getElementById('ms-pano-controls');
        if (controls) controls.hidden = true;
        if (pano) this.viewerStatus('');
    },

    disposePano(pano) {
        if (!pano) return;
        if (pano.raf) cancelAnimationFrame(pano.raf);
        if (pano.resize) window.removeEventListener('resize', pano.resize);
        if (pano.detach) pano.detach();
        try { pano.scene.remove(pano.mesh); } catch (err) { /* 忽略：资源已释放 */ }
        try { pano.geometry.dispose(); } catch (err) { /* 同上 */ }
        try { pano.texture.dispose(); } catch (err) { /* 同上 */ }
        try { pano.material.dispose(); } catch (err) { /* 同上 */ }
        const canvas = pano.renderer && pano.renderer.domElement;
        try { pano.renderer.dispose(); } catch (err) { /* 同上 */ }
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
    },

    viewerStatus(msg) {
        const el = document.getElementById('ms-viewer-status');
        if (!el) return;
        el.textContent = msg || '';
        el.hidden = !msg;
    },

    closeViewer() {
        const viewer = this._viewer;
        if (!viewer) return;
        this._viewer = null;
        viewer.seq += 1;
        if (this._viewerKeys) {
            document.removeEventListener('keydown', this._viewerKeys);
            this._viewerKeys = null;
        }
        const pano = viewer.pano;
        viewer.pano = null;
        this.disposePano(pano);
        if (viewer.box && viewer.box.parentNode) viewer.box.parentNode.removeChild(viewer.box);
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
