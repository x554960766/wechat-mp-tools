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
    _nsfwItems: [],
    _nsfwTotal: 0,
    _roots: {},
    _scanRootId: '',
    _scanJobId: null,
    _scanTimer: null,
    _nsfwTimer: null,
    // 一次性检测（probe）：不写索引，结果只服务本卡
    _probeScope: 'library',
    _probeJobId: null,
    _probeTimer: null,
    _probeItems: [],
    _probeTotal: 0,
    _probeOffset: 0,
    _probeFlaggedOnly: false,
    _probeCheck: null,
    _probeBusy: false,
    // 卡片折叠：默认只折清单（一进页面就摊开 50 行太长）；状态存 localStorage，切页回来不重置
    _cardState: null,
    _collapsedByDefault: ['list'],

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

    /** 图片行的 NSFW 徽标：有效结论为 nsfw 时给出分数，safe 或无结论不显示，避免噪声。 */
    _nsfwBadge(item) {
        if (!item || item.kind !== 'image' || !item.nsfw || item.nsfw.label !== 'nsfw') return '';
        const score = item.nsfw.score != null ? Number(item.nsfw.score).toFixed(2) : '—';
        const reviewed = item.nsfw.stage === 'second';
        const cls = reviewed ? 'files-badge files-badge-reviewed' : 'files-badge files-badge-flagged';
        return `<span class="${cls}">${reviewed ? '已复核' : '疑似'} ${this.esc(score)}</span>`;
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
                <div class="card" data-card="stats">
                    <div class="card-header"><h3 class="card-title">清单概览</h3></div>
                    <div id="files-stats-body"><p class="form-hint">加载中…</p></div>
                </div>
                <div class="card" data-card="dupStats">
                    <div class="card-header"><h3 class="card-title">重复情况</h3></div>
                    <div id="files-dups-body"><p class="form-hint">加载中…</p></div>
                </div>
            </div>

            <div class="card" data-card="filters">
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
                    <div class="form-group">
                        <label class="form-label" for="files-nsfw">NSFW</label>
                        <select class="form-input" id="files-nsfw">
                            <option value="">全部</option>
                            <option value="flagged">只看疑似敏感</option>
                            <option value="unreviewed">只看待复核</option>
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

            <div class="card" data-card="index">
                <div class="card-header"><h3 class="card-title">索引管理</h3></div>
                <div class="files-grid">
                    <div class="form-group">
                        <label class="form-label" for="files-scan-root">扫描来源</label>
                        <select class="form-input" id="files-scan-root"><option value="">读取中…</option></select>
                    </div>
                    <div class="form-group" style="justify-content:flex-end;">
                        <div class="btn-group">
                            <button class="btn btn-secondary" id="files-scan">扫描新增</button>
                            <button class="btn btn-secondary" id="files-rescan">重建索引</button>
                        </div>
                    </div>
                </div>
                <p class="form-hint">扫描会把所选媒体根里的文件重新入库。索引变化后，本页的概览、清单与重复组会自动刷新。</p>
                <span class="form-hint" id="files-scan-state"></span>
            </div>

            <style>
                .files-probe-drop {
                    border: 2px dashed rgba(102, 126, 234, 0.35);
                    border-radius: var(--radius-lg);
                    padding: 24px 16px;
                    text-align: center;
                    cursor: pointer;
                    transition: all var(--transition-normal);
                    background: var(--bg-card, #fff);
                }
                .files-probe-drop.is-over {
                    border-color: var(--primary);
                    background: rgba(102, 126, 234, 0.06);
                }
                .files-probe-drop-title { font-weight: 600; color: var(--text-primary); }
                .files-probe-drop-hint { font-size: 0.82rem; color: var(--text-muted); margin-top: 6px; }
                .files-probe-hits { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }
                .files-probe-hit { text-align: center; font-size: 0.72rem; color: var(--text-muted); }
                .files-probe-hit img {
                    width: 64px; height: 48px; object-fit: cover;
                    border-radius: var(--radius-sm, 4px); display: block; margin-bottom: 2px;
                }
                .files-probe-hit.is-top img { outline: 2px solid var(--error, #e5484d); }
                .files-probe-err { color: var(--error); font-size: 0.8rem; }
                .files-probe-bar {
                    display: inline-block; height: 4px; border-radius: 2px;
                    background: var(--primary); vertical-align: middle; margin-right: 6px;
                    transition: width var(--transition-normal);
                }
            </style>

            <div class="card" data-card="probe">
                <div class="card-header">
                    <h3 class="card-title">内容审核（NSFW）</h3>
                    <div class="btn-group" id="files-probe-scope">
                        <button class="btn btn-primary btn-sm" data-scope="library">媒体库</button>
                        <button class="btn btn-secondary btn-sm" data-scope="path">任意目录</button>
                        <button class="btn btn-secondary btn-sm" data-scope="upload">单个文件</button>
                    </div>
                </div>
                <p class="form-hint" id="files-nsfw-state"></p>

                <div data-panel="library">
                    <div class="btn-group">
                        <button class="btn btn-secondary btn-sm" id="files-nsfw-scan">检测未判定</button>
                        <button class="btn btn-secondary btn-sm" id="files-nsfw-rescan">全部重检</button>
                    </div>
                    <p class="form-hint">只扫已入库索引：结论写入索引、计入覆盖率，判疑似的会进入下方「待复核」队列。</p>
                </div>

                <div data-panel="path" hidden>
                    <div class="files-grid">
                        <div class="form-group">
                            <label class="form-label" for="files-probe-path">目录绝对路径</label>
                            <input class="form-input" id="files-probe-path" placeholder="/Users/you/Pictures" autocomplete="off">
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="files-probe-kinds">检测类型</label>
                            <select class="form-input" id="files-probe-kinds">
                                <option value="">图片 + 视频</option>
                                <option value="image">仅图片</option>
                                <option value="video">仅视频</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="files-probe-interval">视频抽帧间隔</label>
                            <select class="form-input" id="files-probe-interval">
                                <option value="2">2 秒</option>
                                <option value="5" selected>5 秒</option>
                                <option value="10">10 秒</option>
                            </select>
                        </div>
                        <div class="form-group" style="justify-content:flex-end;">
                            <label class="form-label" style="display:flex;align-items:center;gap:6px;margin:0;cursor:pointer;">
                                <input type="checkbox" id="files-probe-recursive" checked> 含子目录
                            </label>
                        </div>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-secondary btn-sm" id="files-probe-check">预检</button>
                        <button class="btn btn-primary btn-sm" id="files-probe-start" disabled>开始检测</button>
                    </div>
                    <p class="form-hint" id="files-probe-check-state">先预检确认路径可读、能扫到多少文件。该范围只本次有效：结果不入索引、不计覆盖率；需要留档的文件在结果里单独「入库」。</p>
                </div>

                <div data-panel="upload" hidden>
                    <div class="files-probe-drop" id="files-probe-drop">
                        <div class="files-probe-drop-title">把图片或视频拖到这里</div>
                        <div class="files-probe-drop-hint">也可以点击选择文件。只检测这一个文件，原件会保留到你决定是否入库为止。</div>
                    </div>
                    <input type="file" id="files-probe-file" accept="image/*,video/*" hidden>
                    <p class="form-hint" id="files-probe-upload-state"></p>
                </div>
            </div>

            <div class="card" id="files-probe-card" hidden>
                <div class="card-header">
                    <h3 class="card-title">本次检测结果</h3>
                    <div class="btn-group">
                        <label class="form-hint" style="display:flex;align-items:center;gap:6px;margin:0;cursor:pointer;">
                            <input type="checkbox" id="files-probe-flagged-only"> 只看疑似
                        </label>
                        <button class="btn btn-secondary btn-sm" id="files-probe-more" hidden>加载更多</button>
                        <button class="btn btn-secondary btn-sm" id="files-probe-cancel" hidden>取消检测</button>
                    </div>
                </div>
                <p class="form-hint" id="files-probe-summary"></p>
                <div class="files-grid" id="files-probe-target" hidden>
                    <div class="form-group">
                        <label class="form-label" for="files-probe-root">入库目标媒体根</label>
                        <select class="form-input" id="files-probe-root"></select>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="files-probe-dir">目标子目录（可空）</label>
                        <input class="form-input" id="files-probe-dir" placeholder="例如 inbox/2026-11" autocomplete="off">
                    </div>
                </div>
                <div id="files-probe-items"></div>
            </div>

            <div class="card" data-card="review">
                <div class="card-header">
                    <h3 class="card-title">待复核（媒体库）</h3>
                    <button class="btn btn-secondary btn-sm" id="files-nsfw-load">加载待复核</button>
                </div>
                <div id="files-nsfw-panel"><p class="form-hint">点击「加载待复核」查看已入库、仍判疑似敏感的图片，可直接确认敏感或标为误报。</p></div>
            </div>

            <div class="card" data-card="list">
                <div class="card-header">
                    <h3 class="card-title">文件清单</h3>
                    <span class="form-hint" id="files-count">—</span>
                </div>
                <div id="files-list"><div class="empty-state"><p class="form-hint">加载中…</p></div></div>
                <div class="btn-group" id="files-pager">
                    <button class="btn btn-secondary" id="files-prev">上一页</button>
                    <span class="files-pager-jump" id="files-page">
                        <input class="form-input files-page-input" id="files-page-input" type="number" min="1" value="1"
                               inputmode="numeric" aria-label="跳到指定页，回车确认" title="输入页码后回车跳转">
                        <span class="form-hint" id="files-page-total">/ 1</span>
                    </span>
                    <button class="btn btn-secondary" id="files-next">下一页</button>
                </div>
            </div>

            <div class="card" data-card="dupGroups">
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
        // 先装折叠：后续 _load* 写入的内容都落在 card-body 里，收起时一起隐藏。
        this._initCards();

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
        const nsfwSel = document.getElementById('files-nsfw');
        if (nsfwSel) nsfwSel.addEventListener('change', () => this._search());
        const extInput = document.getElementById('files-ext');
        if (extInput) extInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') this._search();
        });
        const prevBtn = document.getElementById('files-prev');
        if (prevBtn) prevBtn.addEventListener('click', () => this._goPage(this._page - 1));
        const nextBtn = document.getElementById('files-next');
        if (nextBtn) nextBtn.addEventListener('click', () => this._goPage(this._page + 1));
        // 页码输入：回车直接跳，失焦也跳；值在 _goPage 里统一夹到 [1, 总页数]，输入 999 不会报错。
        const pageInput = document.getElementById('files-page-input');
        if (pageInput) {
            const jump = () => this._goPage(Number(pageInput.value) - 1);
            pageInput.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') { event.preventDefault(); jump(); }
            });
            pageInput.addEventListener('change', jump);
        }

        // 列表内的查看/播放动作用事件委托：清单每次重渲染，逐个绑定会在 DOM 替换后失效。
        const listEl = document.getElementById('files-list');
        if (listEl) listEl.addEventListener('click', (event) => this._onListClick(event));
        // 概览块每次刷新都会整体重渲染，委托绑在固定容器上。
        const statsBody = document.getElementById('files-stats-body');
        if (statsBody) statsBody.addEventListener('click', (event) => this._onStatsClick(event));
        const dupsEl = document.getElementById('files-dups');
        if (dupsEl) dupsEl.addEventListener('click', (event) => this._onDupClick(event));
        const dupsBody = document.getElementById('files-dups-body');
        if (dupsBody) dupsBody.addEventListener('click', (event) => this._onDupStatsClick(event));

        const loadBtn = document.getElementById('files-load-dups');
        if (loadBtn) loadBtn.addEventListener('click', () => this._loadDuplicates());

        const scanBtn = document.getElementById('files-scan');
        if (scanBtn) scanBtn.addEventListener('click', () => this._triggerScan(false));
        const rescanBtn = document.getElementById('files-rescan');
        if (rescanBtn) rescanBtn.addEventListener('click', () => this._triggerScan(true));

        const nsfwLoadBtn = document.getElementById('files-nsfw-load');
        if (nsfwLoadBtn) nsfwLoadBtn.addEventListener('click', () => this._loadNsfw());
        const nsfwScanBtn = document.getElementById('files-nsfw-scan');
        if (nsfwScanBtn) nsfwScanBtn.addEventListener('click', () => this._triggerNsfwScan(false));
        const nsfwRescanBtn = document.getElementById('files-nsfw-rescan');
        if (nsfwRescanBtn) nsfwRescanBtn.addEventListener('click', () => this._triggerNsfwScan(true));
        const nsfwEl = document.getElementById('files-nsfw-panel');
        if (nsfwEl) nsfwEl.addEventListener('click', (event) => this._onNsfwClick(event));
        this._bindProbe();

        await this._loadRoots();
        // 根列表需先于清单与重复组就绪：删除按钮要按根的可写性渲染，否则首次渲染会漏掉按钮。
        await Promise.allSettled([this._loadStats(), this._loadList()]);
        await this._loadDuplicates();
        await this._loadNsfw();
        this._refreshNsfwStatus();
    },

    onShow() {
        // 从别的页返回时刷新概览、清单与待复核：索引可能在别处被扫描或复核更新过。
        Promise.allSettled([this._loadStats(), this._loadList(), this._loadNsfw()]);
        this._refreshNsfwStatus();
    },

    destroy() {
        if (this._scanTimer) {
            clearInterval(this._scanTimer);
            this._scanTimer = null;
        }
        if (this._nsfwTimer) {
            clearInterval(this._nsfwTimer);
            this._nsfwTimer = null;
        }
        if (this._probeTimer) {
            clearInterval(this._probeTimer);
            this._probeTimer = null;
        }
        if (this._viewer) {
            this._viewer.close();
            this._viewer = null;
        }
        this._items = [];
        this._viewerImages = [];
        this._nsfwItems = [];
        this._nsfwTotal = 0;
        this._scanJobId = null;
        this._scanRootId = '';
    },

    async _loadRoots() {
        const sel = document.getElementById('files-root');
        const scanSel = document.getElementById('files-scan-root');
        const probeSel = document.getElementById('files-probe-root');
        if (!sel || !scanSel) return;
        try {
            const data = await API.media.roots();
            const items = (data && data.items) || [];
            this._roots = {};
            items.forEach((root) => {
                // 记录是否可写：删除按钮只对可写根开放，避免前端点了才被后端 403 拒绝。
                this._roots[root.id] = { label: root.label || root.id, writable: !!root.writable };
                // 检索筛选与扫描来源共用同一份根列表，避免两处选项不一致。
                [sel, scanSel].forEach((target) => {
                    const opt = document.createElement('option');
                    opt.value = root.id;
                    opt.textContent = root.label || root.id;
                    target.appendChild(opt);
                });
                // 入库目标只列可写根：只读根写不进去，列出来只会让用户白点一次。
                if (root.writable && probeSel) {
                    const opt = document.createElement('option');
                    opt.value = root.id;
                    opt.textContent = root.label || root.id;
                    probeSel.appendChild(opt);
                }
            });
            // 默认选中第一个根，省一次手动选择。
            if (items.length && !this._scanRootId) {
                this._scanRootId = items[0].id;
                scanSel.value = this._scanRootId;
            }
        } catch (err) {
            if (scanSel) scanSel.innerHTML = '<option value="">读取失败</option>';
            // 根列表取不到不影响检索：留空即「全部根」，不必让用户卡在这。
            console.warn('加载媒体根失败：', err);
        }
    },

    async _loadStats() {
        const body = document.getElementById('files-stats-body');
        if (!body) return;
        try {
            const s = await API.files.stats();
            // 概览里的数字和标签都是下钻入口：点一下就把对应筛选写进检索条件并滚到清单。
            const stat = (key, value, label, attrs = '', title = '点击查看对应文件清单') => `
                <button type="button" class="files-stat files-stat-link" data-stat="${key}" ${attrs} title="${this.esc(title)}">
                    <span class="files-stat-value">${this.esc(value)}</span>
                    <span class="files-stat-label">${label}</span>
                </button>`;
            // 最大文件：接口没有排序参数，但 stats 会给出那一个文件的 root/relpath，
            // 于是把路径写进检索条件精确定位它，而不是做成点了没反应的假按钮。
            const biggest = s.biggest || {};
            const biggestStat = biggest.relpath
                ? stat('biggest', this._formatSize(biggest.size), '最大文件',
                    `data-root="${this.esc(biggest.root || '')}" data-relpath="${this.esc(biggest.relpath)}"`,
                    `点击定位：${biggest.relpath}`)
                : `<div class="files-stat"><span class="files-stat-value">${this.esc(this._formatSize(biggest.size))}</span><span class="files-stat-label">最大文件</span></div>`;
            const kindBits = (s.kinds || [])
                .map((k) => `<button type="button" class="files-pill files-pill-link" data-kind="${this.esc(k.kind)}" title="只看${this.esc(this._kindLabel(k.kind))}">${this.esc(this._kindLabel(k.kind))} ${this.esc(k.count)}</button>`)
                .join(' ');
            const rootBits = (s.roots || [])
                .map((r) => `<button type="button" class="files-pill files-pill-link" data-root="${this.esc(r.root)}" title="只看该媒体根">${this.esc(r.root)} ${this.esc(r.count)}</button>`)
                .join(' ');
            body.innerHTML = `
                <div class="files-stats-rows">
                    ${stat('total', s.total, '已入库文件')}
                    ${biggestStat}
                    ${stat('flagged', s.nsfw_flagged, '疑似敏感')}
                    ${stat('unreviewed', s.nsfw_unreviewed, '待复核')}
                </div>
                <div class="files-stats-block">
                    <div class="files-block-title">按类型</div>
                    <div class="files-pills">${kindBits || '<span class="form-hint">—</span>'}</div>
                </div>
                <div class="files-stats-block">
                    <div class="files-block-title">按媒体根</div>
                    <div class="files-pills">${rootBits || '<span class="form-hint">—</span>'}</div>
                </div>
                <p class="form-hint files-stats-hint">点数字或标签，直接查看对应的文件清单（清单内可查看、删除）。</p>
            `;
        } catch (err) {
            body.innerHTML = '<p class="form-hint" style="color:var(--error)">概览加载失败：' + this.esc(err.message || err) + '</p>';
        }
    },

    /**
     * 概览下钻：按点中的数字/标签把筛选条件写进检索表单，再滚到文件清单。
     * 每次都先清空全部条件，避免和上一次下钻叠加成空结果。
     */
    // ── 卡片折叠 ────────────────────────────────────────────
    /** 给每张卡片装一个折叠按钮，并把内容包进 .card-body 以便整块收起。 */
    _initCards() {
        this._cardState = this._readCardState();
        document.querySelectorAll('[data-card]').forEach((card) => {
            const key = card.dataset.card;
            const header = card.querySelector(':scope > .card-header');
            // 重复进入（例如页面重挂载）时不要套两层 body
            if (!header || card.querySelector(':scope > .card-body')) return;
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'card-toggle';
            btn.dataset.cardToggle = key;
            btn.setAttribute('aria-expanded', 'true');
            btn.innerHTML = '<span class="card-toggle-icon" aria-hidden="true">▸</span>';
            btn.addEventListener('click', () => this._toggleCard(key));
            // 头部是 space-between：按钮和标题包成一组，右侧的额外按钮才不会被推到中间。
            const main = document.createElement('div');
            main.className = 'card-head-main';
            main.appendChild(btn);
            const title = header.querySelector('.card-title');
            if (title) main.appendChild(title);
            header.prepend(main);
            // 卡片外壳不会被整体重渲染（只有内部列表换 innerHTML），所以包一层就够。
            const body = document.createElement('div');
            body.className = 'card-body';
            body.dataset.cardBody = key;
            while (header.nextSibling) body.appendChild(header.nextSibling);
            card.appendChild(body);
            const remembered = this._cardState[key];
            const expanded = typeof remembered === 'boolean'
                ? remembered
                : this._collapsedByDefault.indexOf(key) === -1;
            this._setCardExpanded(key, expanded, false);
        });
    },

    _readCardState() {
        try {
            return JSON.parse(window.localStorage.getItem('files:cardState') || '{}') || {};
        } catch (err) {
            return {};
        }
    },

    _saveCardState() {
        try {
            window.localStorage.setItem('files:cardState', JSON.stringify(this._cardState || {}));
        } catch (err) {
            // 隐私模式或配额满时折叠状态不入库，不影响功能
        }
    },

    _toggleCard(key) {
        const card = document.querySelector(`[data-card="${key}"]`);
        const collapsed = !!(card && card.classList.contains('is-collapsed'));
        // 手动开合之后，上次跳转的「同一入口再点一次就收起」判定失效，否则下一次点数字会先收起来。
        this._lastJumpCard = null;
        this._setCardExpanded(key, collapsed);
    },

    /** 展开/折叠单张卡片；persist=false 时只改 DOM（初始化用）。 */
    _setCardExpanded(key, expanded, persist = true) {
        const card = document.querySelector(`[data-card="${key}"]`);
        if (!card) return;
        card.classList.toggle('is-collapsed', !expanded);
        const body = card.querySelector(`[data-card-body="${key}"]`);
        if (body) body.hidden = !expanded;
        const btn = card.querySelector(`[data-card-toggle="${key}"]`);
        if (btn) {
            btn.setAttribute('aria-expanded', String(expanded));
            btn.title = expanded ? '折叠' : '展开';
        }
        if (persist) {
            this._cardState = Object.assign({}, this._cardState, { [key]: expanded });
            this._saveCardState();
        }
    },

    _scrollToCard(key) {
        const card = document.querySelector(`[data-card="${key}"]`);
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },

    _onStatsClick(event) {
        const act = event.target.closest('[data-stat], [data-kind], [data-root]');
        if (!act) return;
        const stat = document.getElementById('files-stats-body');
        if (!stat || !stat.contains(act)) return;
        const set = (id, value) => {
            const el = document.getElementById(id);
            if (el) el.value = value;
        };
        set('files-q', '');
        set('files-ext', '');
        set('files-dup', '');
        set('files-kind', '');
        set('files-root', '');
        set('files-nsfw', '');
        if (act.dataset.stat === 'flagged') set('files-nsfw', 'flagged');
        else if (act.dataset.stat === 'unreviewed') set('files-nsfw', 'unreviewed');
        else if (act.dataset.stat === 'biggest') {
            // 用 root + 完整相对路径精确定位那一个文件；q 是相对路径模糊匹配，整条路径必然只命中它自己。
            set('files-root', act.dataset.root || '');
            set('files-q', act.dataset.relpath || '');
        }
        else if (act.dataset.kind) set('files-kind', act.dataset.kind);
        else if (act.dataset.root) set('files-root', act.dataset.root);
        this._search();
        // 清单默认折叠：先展开再滚过去，否则跳过去只看得见一张卡片标题。
        if (typeof act.blur === 'function') act.blur();
        const token = act.dataset.stat
            || (act.dataset.kind ? 'kind:' + act.dataset.kind : '')
            || (act.dataset.root ? 'root:' + act.dataset.root : '');
        this._jumpToCard('list', token || 'list');
    },

    /** 「重复情况」卡里的数字：两个都指向下方分组明细，同样先展开再定位。 */
    _onDupStatsClick(event) {
        const act = event.target.closest('[data-jump]');
        if (!act) return;
        if (typeof act.blur === 'function') act.blur();
        this._jumpToCard(act.dataset.jump, 'jump:' + act.dataset.jump);
    },

    /** 跳到目标卡片：折叠时先展开再滚过去，否则只看见一个标题。
     *  再点同一个入口时反过来收起（token 标识是哪块数字/标签，避免「已入库文件」与
     *  「疑似敏感」都跳到清单、却被当成同一个入口而互相收起）。 */
    _jumpToCard(key, token = key) {
        const card = document.querySelector(`[data-card="${key}"]`);
        const expanded = !!(card && !card.classList.contains('is-collapsed'));
        if (expanded && this._lastJumpCard === token) {
            this._setCardExpanded(key, false);
            this._scrollToCard(key);
            this._lastJumpCard = null;
            return;
        }
        this._setCardExpanded(key, true);
        this._scrollToCard(key);
        this._lastJumpCard = token;
    },

    async _loadList() {
        const listEl = document.getElementById('files-list');
        const countEl = document.getElementById('files-count');
        const pagerEl = document.getElementById('files-pager');
        const prevEl = document.getElementById('files-prev');
        const nextEl = document.getElementById('files-next');
        if (!listEl) return;
        const params = {
            q: (document.getElementById('files-q') || {}).value || undefined,
            root: (document.getElementById('files-root') || {}).value || undefined,
            ext: (document.getElementById('files-ext') || {}).value || undefined,
            kind: (document.getElementById('files-kind') || {}).value || undefined,
            duplicates: (document.getElementById('files-dup') || {}).value || undefined,
            nsfw: (document.getElementById('files-nsfw') || {}).value || undefined,
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
        // 页码框跟着真实状态走：刷新清单后输入框里的数字必须等于当前页，否则用户会拿着旧页码跳错。
        const pageInput = document.getElementById('files-page-input');
        const pageTotal = document.getElementById('files-page-total');
        if (pageInput) {
            pageInput.value = String(this._page + 1);
            pageInput.max = String(totalPages);
            pageInput.disabled = totalPages <= 1;
        }
        if (pageTotal) pageTotal.textContent = `/ ${totalPages}`;
        if (prevEl) prevEl.disabled = this._page <= 0;
        if (nextEl) nextEl.disabled = this._page >= totalPages - 1;
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
            const nsfwBadge = this._nsfwBadge(item);
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
                        <div class="files-actions">${dupBadge}${nsfwBadge}${actions}${delBtn}</div>
                    </div>
                </div>`;
        }).join('');
    },

    _onListClick(event) {
        const act = event.target.closest('[data-act]');
        if (!act) return;
        const index = Number(act.dataset.index);
        if (act.dataset.act === 'view') this._openViewer(this._viewerImages, index);
        else if (act.dataset.act === 'pano') this._openViewer(this._viewerImages, index, true);
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

    // ── 手动加索引（扫描媒体根） ────────────────────────────
    /** 提交一次扫描：rebuild=true 重建索引，false 只补新增与更新。 */
    async _triggerScan(rebuild) {
        const root = this._scanRootId || (document.getElementById('files-scan-root') || {}).value || '';
        if (!root) {
            Toast.error('请先选择要扫描的媒体根');
            return;
        }
        const state = document.getElementById('files-scan-state');
        if (state) state.textContent = '正在提交扫描…';
        try {
            const job = await API.media.scan(root, rebuild);
            Toast.info('扫描任务已提交');
            this._pollScan(job.id);
        } catch (err) {
            if (state) state.textContent = '';
            Toast.error('扫描提交失败：' + (err.message || err));
        }
    },

    /** 轮询扫描任务：完成后自动刷新概览、清单与重复组，避免用户手动刷新漏掉新入库文件。 */
    _pollScan(jobId) {
        if (this._scanTimer) clearInterval(this._scanTimer);
        const tick = async () => {
            const state = document.getElementById('files-scan-state');
            try {
                const job = await API.media.scanStatus(jobId);
                if (!job) return;
                if (state) {
                    state.textContent = `扫描中：已处理 ${job.scanned || 0} 个文件${job.current ? '（' + job.current + '）' : ''}`;
                }
                if (['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
                    if (this._scanTimer) { clearInterval(this._scanTimer); this._scanTimer = null; }
                    if (state) {
                        state.textContent = job.status === 'completed'
                            ? `扫描完成：图片新增 ${job.indexed_new || 0}、更新 ${job.indexed_updated || 0}、未变 ${job.indexed_unchanged || 0}`
                            : `扫描结束（${job.status}）`;
                    }
                    // 索引变了，三处数据一起刷新。
                    await Promise.allSettled([this._loadStats(), this._loadList()]);
                    await this._loadDuplicates();
                }
            } catch (err) {
                if (this._scanTimer) { clearInterval(this._scanTimer); this._scanTimer = null; }
                if (state) state.textContent = '扫描状态读取失败';
            }
        };
        this._scanTimer = setInterval(tick, 1200);
        tick();
    },

    // ── 全局 NSFW 检测 ──────────────────────────────────
    /** 发起全局检测：只补机器初筛结论；force=true 重检全部（换模型/调阈值后用）。 */
    async _triggerNsfwScan(force) {
        const state = document.getElementById('files-nsfw-state');
        const run = async () => {
            try {
                const job = await API.media.nsfwScan({ roots: [], force });
                Toast.info('检测任务已提交');
                this._pollNsfwScan(job.id);
            } catch (err) {
                if (state) state.textContent = '';
                Toast.error('检测提交失败：' + (err.message || err));
            }
        };
        if (force) {
            // 全部重检会覆盖每张图的机器初筛结论，确认一次再动手。
            Modal.confirm('全部重检',
                '会对所有已入库图片重新跑 NSFW 模型，<strong>覆盖现有机器结论</strong>（人工复核结论保留）。继续吗？',
                run);
        } else {
            run();
        }
    },

    _pollNsfwScan(jobId) {
        if (this._nsfwTimer) clearInterval(this._nsfwTimer);
        const tick = async () => {
            const state = document.getElementById('files-nsfw-state');
            try {
                const job = await API.media.nsfwScanStatus(jobId);
                if (!job) return;
                if (state) {
                    state.textContent = `检测中：${job.scanned || 0}/${job.total || 0}，判敏感 ${job.flagged || 0}，失败 ${job.failed || 0}`;
                }
                if (['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
                    if (this._nsfwTimer) { clearInterval(this._nsfwTimer); this._nsfwTimer = null; }
                    const note = job.status === 'completed'
                        ? `检测完成：打分 ${job.scored || 0}/${job.total || 0}，判敏感 ${job.flagged || 0}${job.skipped ? '，跳过 ' + job.skipped : ''}`
                        : `检测结束（${job.status}）：打分 ${job.scored || 0}/${job.total || 0}`;
                    if (state) state.textContent = note;
                    Toast.info(note);
                    // 新结论可能改变筛选结果与待复核列表，一并刷新。
                    await Promise.allSettled([this._loadStats(), this._loadList(), this._loadNsfw()]);
                }
            } catch (err) {
                if (this._nsfwTimer) { clearInterval(this._nsfwTimer); this._nsfwTimer = null; }
                if (state) state.textContent = '检测状态读取失败';
            }
        };
        this._nsfwTimer = setInterval(tick, 1200);
        tick();
    },

    /** 读取覆盖率与模型可用性：未装模型时提前提示，避免点了按钮才发现不能跑。 */
    async _refreshNsfwStatus() {
        const state = document.getElementById('files-nsfw-state');
        // 检测进行中（媒体库或 probe）不覆盖轮询写下的文案。
        if (!state || this._nsfwTimer || this._probeTimer) return;
        // 覆盖率只对「媒体库」有意义：另外两个范围是一次性检测，不参与统计。
        if (this._probeScope !== 'library') {
            state.textContent = '';
            return;
        }
        try {
            const data = await API.media.nsfwStatus({ showError: false });
            if (!data) return;
            if (!data.model) {
                state.textContent = data.enabled
                    ? 'NSFW 模型未安装（data/nsfw.onnx），无法发起检测'
                    : 'NSFW 检测已关闭（AI_MEDIA_NSFW_SCAN=0）';
            } else {
                state.textContent = `已打分 ${data.scored || 0}/${data.total || 0}，待检 ${data.pending || 0}；疑似敏感 ${data.unreviewed || 0} 待复核`;
            }
        } catch {
            // 状态读不到就不占位，面板里已有各自的加载提示。
        }
    },

    // ── NSFW 复核 ───────────────────────────────────────
    /** 加载仍判疑似、且未被人工复核的图片；second 级结论会使其出列。 */
    async _loadNsfw() {
        const wrap = document.getElementById('files-nsfw-panel');
        if (!wrap) return;
        wrap.innerHTML = '<p class="form-hint">加载中…</p>';
        try {
            const data = await API.media.nsfwFlagged({ include_reviewed: false, limit: 60 }, { showError: false });
            this._nsfwItems = (data && data.items) || [];
            this._nsfwTotal = (data && data.total) || this._nsfwItems.length;
            this._renderNsfw();
        } catch (err) {
            wrap.innerHTML = `<p class="form-hint" style="color:var(--error)">待复核加载失败：${this.esc(err.message || err)}</p>`;
        }
    },

    _renderNsfw() {
        const wrap = document.getElementById('files-nsfw-panel');
        if (!wrap) return;
        if (!this._nsfwItems.length) {
            wrap.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">🛡️</div>
                    <h3 class="empty-state-title">没有待复核的图片</h3>
                    <p class="empty-state-desc">当前索引里没有仍判疑似敏感、且未被人工复核过的图片。</p>
                </div>`;
            return;
        }
        wrap.innerHTML = `<p class="form-hint">待复核 ${this.esc(this._nsfwTotal)} 张（按分数从高到低，可直接确认敏感或标为误报）</p>
            <div class="files-nsfw-grid">${this._nsfwItems.map((item, index) => `
                <div class="files-nsfw-card">
                    <img class="files-thumb" src="${this.esc(this._thumbUrl(item))}" loading="lazy" alt="">
                    <div class="files-nsfw-body">
                        <div class="files-path" title="${this.esc(item.relpath)}">${this.esc(item.relpath)}</div>
                        <div class="files-meta">
                            <span class="files-badge files-badge-flagged">疑似 ${this.esc(Number(item.score != null ? item.score : 0).toFixed(2))}</span>
                            <span class="files-meta-item">根：${this.esc(item.root)}</span>
                        </div>
                    </div>
                    <div class="files-actions">
                        <button class="btn btn-secondary btn-sm" data-act="view" data-index="${index}">普通查看</button>
                        <button class="btn btn-secondary btn-sm" data-act="pano" data-index="${index}">VR 全景</button>
                        <button class="btn btn-secondary btn-sm" data-act="review-nsfw" data-root="${this.esc(item.root)}" data-relpath="${this.esc(item.relpath)}">确认敏感</button>
                        <button class="btn btn-secondary btn-sm" data-act="review-safe" data-root="${this.esc(item.root)}" data-relpath="${this.esc(item.relpath)}">标为误报</button>
                    </div>
                </div>`).join('')}</div>`;
    },

    _onNsfwClick(event) {
        const act = event.target.closest('[data-act]');
        if (!act) return;
        const index = Number(act.dataset.index);
        if (act.dataset.act === 'view') this._openViewer(this._nsfwItems, index);
        else if (act.dataset.act === 'pano') this._openViewer(this._nsfwItems, index, true);
        else if (act.dataset.act === 'review-nsfw' || act.dataset.act === 'review-safe') {
            this._reviewNsfw(act.dataset.root, act.dataset.relpath,
                act.dataset.act === 'review-nsfw' ? 'nsfw' : 'safe');
        }
    },

    /** 复核写 second 级结论，图片随即从「待复核」出列；清单徽标、概览计数同步刷新。 */
    async _reviewNsfw(root, relpath, label) {
        try {
            await API.media.nsfwReview({ root, relpath, label, reviewed_by: '本机' }, { showError: false });
            Toast.success(`已${label === 'nsfw' ? '确认敏感' : '标为误报'}：${root} / ${relpath}`);
            await Promise.allSettled([this._loadStats(), this._loadList(), this._loadNsfw()]);
        } catch (err) {
            Toast.error('复核失败：' + (err.message || err));
        }
    },

    // ── 一次性检测（任意目录 / 单个文件）──────────────────
    /**
     * 与「媒体库」检测的差别只有一个：结果不写索引。
     * 因此这里不碰 nsfw 表、不动覆盖率，只把 job 作用域的结果文件渲染出来；
     * 需要留档的文件走「入库」→ 复制进媒体根 → 入队一次增量扫描。
     */
    _bindProbe() {
        const scopeWrap = document.getElementById('files-probe-scope');
        if (scopeWrap) {
            scopeWrap.addEventListener('click', (event) => {
                const btn = event.target.closest('[data-scope]');
                if (btn) this._setProbeScope(btn.dataset.scope);
            });
        }
        const checkBtn = document.getElementById('files-probe-check');
        if (checkBtn) checkBtn.addEventListener('click', () => this._probePrecheck());
        const startBtn = document.getElementById('files-probe-start');
        if (startBtn) startBtn.addEventListener('click', () => this._startProbePath());

        const pathInput = document.getElementById('files-probe-path');
        if (pathInput) {
            // 改了路径或筛选条件，旧预检结果就不再对应当前目录，必须重新预检。
            pathInput.addEventListener('input', () => this._invalidateProbeCheck());
            pathInput.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') this._probePrecheck();
            });
            try {
                const saved = localStorage.getItem('files_probe_path');
                if (saved) pathInput.value = saved;
            } catch {
                // 隐私模式禁用 localStorage，记住路径只是便利，不影响功能
            }
        }
        ['files-probe-recursive', 'files-probe-kinds'].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', () => this._invalidateProbeCheck());
        });

        const drop = document.getElementById('files-probe-drop');
        const fileInput = document.getElementById('files-probe-file');
        if (drop && fileInput) {
            drop.addEventListener('click', () => fileInput.click());
            ['dragenter', 'dragover'].forEach((type) => {
                drop.addEventListener(type, (event) => {
                    event.preventDefault();
                    drop.classList.add('is-over');
                });
            });
            ['dragleave', 'drop'].forEach((type) => {
                drop.addEventListener(type, () => drop.classList.remove('is-over'));
            });
            drop.addEventListener('drop', (event) => {
                event.preventDefault();
                const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
                if (file) this._startProbeUpload(file);
            });
            fileInput.addEventListener('change', () => {
                const file = fileInput.files && fileInput.files[0];
                fileInput.value = '';   // 允许再次选同一个文件
                if (file) this._startProbeUpload(file);
            });
        }

        const cancelBtn = document.getElementById('files-probe-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', () => this._cancelProbe());
        const moreBtn = document.getElementById('files-probe-more');
        if (moreBtn) moreBtn.addEventListener('click', () => this._loadProbeItems(false));
        const flagged = document.getElementById('files-probe-flagged-only');
        if (flagged) {
            flagged.addEventListener('change', () => {
                this._probeFlaggedOnly = !!flagged.checked;
                this._loadProbeItems(true);
            });
        }
        const itemsEl = document.getElementById('files-probe-items');
        if (itemsEl) itemsEl.addEventListener('click', (event) => this._onProbeItemsClick(event));
    },

    _setProbeScope(scope) {
        if (!['library', 'path', 'upload'].includes(scope)) return;
        this._probeScope = scope;
        const wrap = document.getElementById('files-probe-scope');
        if (wrap) {
            wrap.querySelectorAll('[data-scope]').forEach((btn) => {
                const active = btn.dataset.scope === scope;
                btn.classList.toggle('btn-primary', active);
                btn.classList.toggle('btn-secondary', !active);
            });
            const card = wrap.closest('.card');
            if (card) {
                card.querySelectorAll('[data-panel]').forEach((panel) => {
                    panel.hidden = panel.dataset.panel !== scope;
                });
            }
        }
        this._refreshNsfwStatus();
    },

    _invalidateProbeCheck() {
        if (!this._probeCheck) return;
        this._probeCheck = null;
        const startBtn = document.getElementById('files-probe-start');
        if (startBtn) startBtn.disabled = true;
        const state = document.getElementById('files-probe-check-state');
        if (state) state.textContent = '条件已改，请重新预检';
    },

    /** 预检只读目录元数据：先让用户看到规模再决定扫不扫，避免点下去才发现是几万个文件。 */
    async _probePrecheck() {
        const pathInput = document.getElementById('files-probe-path');
        const state = document.getElementById('files-probe-check-state');
        const startBtn = document.getElementById('files-probe-start');
        const raw = pathInput ? pathInput.value.trim() : '';
        this._probeCheck = null;
        if (startBtn) startBtn.disabled = true;
        if (!raw) {
            if (state) state.textContent = '请先填写目录绝对路径';
            return;
        }
        if (!raw.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(raw)) {
            // 相对路径相对谁无解，猜一个只会扫错目录。
            if (state) state.textContent = '请填绝对路径（以 / 开头）';
            return;
        }
        if (state) state.textContent = '预检中…';
        try {
            const recursive = !!(document.getElementById('files-probe-recursive') || {}).checked;
            const check = await API.media.nsfwProbeCheck({ path: raw, recursive, max_files: 5000 });
            this._probeCheck = check;
            try {
                localStorage.setItem('files_probe_path', raw);
            } catch {
                // 同 _bindProbe：存不下不影响检测
            }
            if (state) state.textContent = this._describeCheck(check);
            if (startBtn) startBtn.disabled = !(check && check.total > 0 && !check.error);
        } catch (err) {
            if (state) state.textContent = '预检失败：' + (err.message || err);
        }
    },

    _describeCheck(check) {
        if (!check) return '';
        if (check.error) return `无法检测：${check.error}`;
        if (!check.total) return '这个路径下没有可检测的图片或视频（检查扩展名与「含子目录」选项）';
        const parts = [`可扫 ${check.total} 个文件（图片 ${check.images}、视频 ${check.videos}）`];
        if (!check.recursive) parts.push('不含子目录');
        let text = parts.join('，');
        if (check.truncated) text += `。已达上限，只检测前 ${check.max_files} 个，其余请分批或换更窄的目录`;
        if (Array.isArray(check.sample) && check.sample.length) {
            text += `。示例：${check.sample.slice(0, 3).join('、')}`;
        }
        return text;
    },

    async _startProbePath() {
        const check = this._probeCheck;
        if (!check || !check.total || this._probeBusy) return;
        const kindsSel = (document.getElementById('files-probe-kinds') || {}).value || '';
        const interval = Number((document.getElementById('files-probe-interval') || {}).value || 5);
        const run = async () => {
            this._probeBusy = true;
            try {
                // recursive 取预检时的值：用户预检后又改了勾选但没重检，扫的范围会与看到的数字不一致。
                const job = await API.media.nsfwProbeStart({
                    path: check.path,
                    recursive: !!check.recursive,
                    kinds: kindsSel ? [kindsSel] : ['image', 'video'],
                    video_interval: interval,
                    max_files: 5000,
                });
                this._beginProbe(job);
            } catch (err) {
                Toast.error('检测提交失败：' + (err.message || err));
            } finally {
                this._probeBusy = false;
            }
        };
        if (check.truncated) {
            Modal.confirm('只检测前 5000 个文件',
                `这个目录里的可检测文件已达上限，本次只会处理前 <strong>${this.esc(check.max_files)}</strong> 个。继续吗？`,
                run);
        } else {
            run();
        }
    },

    async _startProbeUpload(file) {
        if (this._probeBusy) return;
        const state = document.getElementById('files-probe-upload-state');
        const interval = Number((document.getElementById('files-probe-interval') || {}).value || 5);
        const write = (text, percent) => {
            if (!state) return;
            const bar = percent == null ? ''
                : `<span class="files-probe-bar" style="width:${Math.max(0, Math.min(100, percent))}px"></span>`;
            state.innerHTML = bar + this.esc(text);
        };
        this._probeBusy = true;
        write(`上传中… ${this._formatSize(file.size)}`, 0);
        try {
            const job = await new Promise((resolve, reject) => {
                const form = new FormData();
                form.append('file', file);
                form.append('video_interval', String(interval));
                const xhr = new XMLHttpRequest();
                xhr.open('POST', '/media/api/nsfw/probes/upload');
                xhr.upload.onprogress = (event) => {
                    if (!event.lengthComputable) return;
                    const percent = Math.round((event.loaded / event.total) * 100);
                    write(`上传中… ${percent}%（${this._formatSize(event.loaded)} / ${this._formatSize(event.total)}）`, percent);
                };
                xhr.onload = () => {
                    let data = {};
                    try {
                        data = JSON.parse(xhr.responseText || '{}');
                    } catch {
                        data = {};
                    }
                    if (xhr.status >= 200 && xhr.status < 300 && data.id) resolve(data);
                    else {
                        const nested = data && data.error;
                        const message = (typeof nested === 'string' && nested)
                            || (nested && (nested.message || nested.code))
                            || data.detail
                            || `HTTP ${xhr.status}`;
                        reject(new Error(message));
                    }
                };
                xhr.onerror = () => reject(new Error('上传中断，请检查服务是否运行'));
                xhr.send(form);
            });
            write(`已上传：${file.name}`);
            this._beginProbe(job);
        } catch (err) {
            write('上传失败：' + (err.message || err));
            Toast.error('上传失败：' + (err.message || err));
        } finally {
            this._probeBusy = false;
        }
    },

    /** 任务起来后统一走「本次检测结果」卡：一次只保留一个任务，避免结果区互相覆盖。 */
    _beginProbe(job) {
        if (!job || !job.id) {
            Toast.error('检测任务创建失败');
            return;
        }
        this._probeJobId = job.id;
        this._probeItems = [];
        this._probeTotal = 0;
        this._probeOffset = 0;
        this._probeFlaggedOnly = false;
        const card = document.getElementById('files-probe-card');
        if (card) card.hidden = false;
        const flagged = document.getElementById('files-probe-flagged-only');
        if (flagged) flagged.checked = false;
        const more = document.getElementById('files-probe-more');
        if (more) more.hidden = true;
        const target = document.getElementById('files-probe-target');
        if (target) target.hidden = true;
        const itemsEl = document.getElementById('files-probe-items');
        if (itemsEl) itemsEl.innerHTML = '<p class="form-hint">检测中…</p>';
        const summary = document.getElementById('files-probe-summary');
        if (summary) summary.textContent = `任务 ${job.id} 已提交`;
        this._pollProbe(job.id);
    },

    _pollProbe(jobId) {
        if (this._probeTimer) clearInterval(this._probeTimer);
        const tick = async () => {
            const summary = document.getElementById('files-probe-summary');
            const cancelBtn = document.getElementById('files-probe-cancel');
            try {
                const job = await API.media.nsfwProbeStatus(jobId);
                if (!job || this._probeJobId !== jobId) return;
                const done = ['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status);
                const counts = `已检 ${job.scanned || 0}/${job.total || 0}，判疑似 ${job.flagged || 0}`
                    + `，跳过 ${job.skipped || 0}，失败 ${job.failed || 0}`
                    + (job.imported ? `，已入库 ${job.imported}` : '');
                if (summary) {
                    summary.textContent = done
                        ? `${this._probeStatusLabel(job.status)}：${counts}`
                        : `${job.phase || '检测中'}：${counts}${job.current ? '（' + job.current + '）' : ''}`;
                }
                if (cancelBtn) cancelBtn.hidden = done;
                if (!done) return;
                if (this._probeTimer) {
                    clearInterval(this._probeTimer);
                    this._probeTimer = null;
                }
                this._probeTotal = job.total || 0;
                await this._loadProbeItems(true);
                if (job.errors && job.errors.length) {
                    Toast.info(`${job.errors.length} 个文件失败，明细见结果列表`);
                }
            } catch (err) {
                if (this._probeTimer) {
                    clearInterval(this._probeTimer);
                    this._probeTimer = null;
                }
                if (summary) summary.textContent = '检测状态读取失败';
            }
        };
        this._probeTimer = setInterval(tick, 1200);
        tick();
    },

    _probeStatusLabel(status) {
        if (status === 'completed') return '检测完成';
        if (status === 'partial') return '检测完成（部分文件失败）';
        if (status === 'cancelled') return '已取消，已出结果仍可入库';
        if (status === 'failed') return '检测失败';
        if (status === 'interrupted') return '检测中断';
        return status || '结束';
    },

    async _cancelProbe() {
        if (!this._probeJobId) return;
        try {
            await API.media.nsfwProbeCancel(this._probeJobId);
            Toast.info('已请求取消检测');
        } catch (err) {
            Toast.error('取消失败：' + (err.message || err));
        }
    },

    async _loadProbeItems(reset) {
        const jobId = this._probeJobId;
        if (!jobId) return;
        if (reset) {
            this._probeOffset = 0;
            this._probeItems = [];
        }
        const itemsEl = document.getElementById('files-probe-items');
        if (reset && itemsEl) itemsEl.innerHTML = '<p class="form-hint">加载结果…</p>';
        try {
            const data = await API.media.nsfwProbeItems(jobId, {
                offset: this._probeOffset,
                limit: 24,
                flagged: this._probeFlaggedOnly ? 1 : undefined,
            }, { showError: false });
            if (jobId !== this._probeJobId) return;   // 期间换了任务，旧结果丢弃
            const items = (data && data.items) || [];
            this._probeTotal = (data && data.total) || 0;
            this._probeItems = reset ? items : this._probeItems.concat(items);
            this._probeOffset = this._probeItems.length;
            this._renderProbeItems();
        } catch (err) {
            if (itemsEl) {
                itemsEl.innerHTML = `<p class="form-hint" style="color:var(--error)">结果加载失败：${this.esc(err.message || err)}</p>`;
            }
        }
    },

    _renderProbeItems() {
        const wrap = document.getElementById('files-probe-items');
        const more = document.getElementById('files-probe-more');
        const target = document.getElementById('files-probe-target');
        if (!wrap) return;
        if (!this._probeItems.length) {
            wrap.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-title">${this._probeFlaggedOnly ? '没有疑似敏感的文件' : '没有结果'}</div>
                    <p class="empty-state-desc">${this._probeFlaggedOnly
                        ? '这次检测里没有判定为敏感的文件。取消勾选「只看疑似」可查看全部结果。'
                        : '这个范围里没有可检测的图片或视频。'}</p>
                </div>`;
            if (more) more.hidden = true;
            return;
        }
        if (target) target.hidden = false;
        wrap.innerHTML = `<div class="files-nsfw-grid">${this._probeItems.map((item) => this._probeCard(item)).join('')}</div>`;
        if (more) more.hidden = this._probeItems.length >= this._probeTotal;
    },

    _probeCard(item) {
        const index = Number(item.index);
        const kindLabel = this._kindLabel(item.kind);
        // 缩略图地址由前端拼：后端只回「有没有缩略图」，不知道宿主把它挂在哪个前缀下。
        const thumb = item.thumb
            ? `<img class="files-thumb" src="${this.esc(this._probeThumbUrl(index))}" loading="lazy" alt="">`
            : `<div class="files-thumb files-thumb-icon">${this._kindIcon(item.kind)}</div>`;
        const status = item.status;
        let badge;
        if (status === 'ok') {
            const score = Number(item.score != null ? item.score : 0).toFixed(2);
            badge = item.label === 'nsfw'
                ? `<span class="files-badge files-badge-flagged">疑似 ${this.esc(score)}</span>`
                : `<span class="files-badge">安全 ${this.esc(score)}</span>`;
        } else if (status === 'skipped') {
            badge = '<span class="files-badge">未判定</span>';
        } else {
            badge = '<span class="files-badge files-badge-flagged">失败</span>';
        }
        const imported = item.imported;
        const importBtn = imported
            ? `<span class="form-hint">已入库：${this.esc(imported.root)} / ${this.esc(imported.relpath)}</span>
               <button class="btn btn-secondary btn-sm" data-act="view" data-index="${index}">查看</button>
               <button class="btn btn-danger btn-sm" data-act="unimport" data-index="${index}">删除</button>`
            : (status === 'ok' || status === 'skipped')
                ? `<button class="btn btn-secondary btn-sm" data-act="import" data-index="${index}">入库</button>`
                : '';
        const errorLine = item.error
            ? `<div class="files-probe-err">${this.esc(item.error)}</div>` : '';
        const videoLine = item.video ? this._probeVideoInfo(item) : '';
        return `<div class="files-nsfw-card">
            ${thumb}
            <div class="files-nsfw-body">
                <div class="files-path" title="${this.esc(item.path)}">${this.esc(item.name || item.path)}</div>
                <div class="files-meta">
                    ${badge}
                    <span class="files-meta-item">${this.esc(kindLabel)}</span>
                    <span class="files-meta-item">${this.esc(this._formatSize(item.size))}</span>
                </div>
                ${videoLine}
                ${errorLine}
            </div>
            <div class="files-actions">${importBtn}</div>
        </div>`;
    },

    /** 视频结论取最严重帧，所以必须给出命中帧与时间，而不是一个总分。 */
    _probeVideoInfo(item) {
        const video = item.video || {};
        const index = Number(item.index);
        const frames = Number(video.frames || 0);
        const scored = Number(video.scored || 0);
        const interval = Number(video.interval || 0);
        const duration = Number(video.duration || 0);
        const head = `<span class="files-meta-item">抽帧 ${this.esc(scored)}/${this.esc(frames)}`
            + `${interval ? '，每 ' + this.esc(interval) + ' 秒一帧' : ''}`
            + `${duration ? '，时长 ' + this.esc(this._formatDuration(duration)) : ''}</span>`;
        const bar = this._probeFramesBar(video.frame_scores);
        const hits = (video.hits || []).map((hit, rank) => `
            <div class="files-probe-hit${rank === 0 ? ' is-top' : ''}">
                ${hit.thumb ? `<img src="${this.esc(API.media.nsfwProbeThumb(this._probeJobId, index, rank))}" loading="lazy" alt="">` : ''}
                <span>${this.esc(this._formatDuration(hit.ts))}</span>
            </div>`).join('');
        return `<div class="files-meta">${head}</div>${bar}
            ${hits ? `<div class="files-probe-hits">${hits}</div>` : ''}`;
    },

    _probeThumbUrl(index) {
        return API.media.nsfwProbeThumb(this._probeJobId, index);
    },

    /** 抽帧时间条：每帧一格，颜色按分数深浅，让「哪一段有问题」一眼看得出来。 */
    _probeFramesBar(scores) {
        if (!Array.isArray(scores) || !scores.length) return '';
        const MAX_CELLS = 60;
        // 超过格数就等距抽样，而不是截断：截断会让时间条只覆盖视频前半段。
        const step = Math.ceil(scores.length / MAX_CELLS);
        const rows = scores.filter((row, i) => i % step === 0);
        const cells = rows.map((row) => {
            const score = Number(row.score || 0);
            const color = score >= 0.6 ? 'var(--error, #e5484d)'
                : score >= 0.3 ? '#e09b3d'
                    : 'rgba(120, 130, 150, 0.35)';
            return `<span style="flex:1;height:100%;background:${color}" title="${this.esc(this._formatDuration(row.ts))} · ${this.esc(score.toFixed(2))}"></span>`;
        }).join('');
        return `<div style="display:flex;gap:1px;height:6px;margin-top:6px;border-radius:3px;overflow:hidden">${cells}</div>`;
    },

    _formatDuration(seconds) {
        const total = Math.max(0, Math.round(Number(seconds) || 0));
        const m = Math.floor(total / 60);
        const s = total % 60;
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    },

    async _onProbeItemsClick(event) {
        const act = event.target.closest('[data-act]');
        if (!act) return;
        const index = Number(act.dataset.index);
        if (act.dataset.act === 'view') { this._viewProbeItem(index); return; }
        if (act.dataset.act === 'unimport') { await this._unimportProbeItem(index, act); return; }
        if (act.dataset.act !== 'import') return;
        await this._importProbeItem(index, act);
    },

    /**
     * 查看已入库副本：图片走灯箱、视频新窗口。
     * 不能改用 /api/media/file —— 那个地址要求索引行，刚入库还没扫描完必然 404。
     */
    _viewProbeItem(index) {
        const jobId = this._probeJobId;
        const item = this._probeItems.find((row) => Number(row.index) === index);
        if (!jobId || !item || !item.imported) return;
        const url = API.media.nsfwProbeFile(jobId, index);
        if (item.kind === 'video') {
            const link = document.createElement('a');
            link.href = url;
            link.target = '_blank';
            link.rel = 'noopener';
            link.click();
            return;
        }
        // 传 file 让查看器直接用后端地址，不再按 root/relpath 拼版本参数。
        if (this._viewer) this._viewer.open([{ file: url, name: item.name }], 0, {});
    },

    // 撤销入库：删副本 + 清索引 + 清结果里的入库标记；检测结果保留，可重新入库。
    async _unimportProbeItem(index, btn) {
        const jobId = this._probeJobId;
        const item = this._probeItems.find((row) => Number(row.index) === index);
        if (!jobId || !item || !item.imported) return;
        const { root, relpath } = item.imported;
        Modal.confirm(
            '删除已入库副本',
            `将从媒体根删除：<br><code>${this.esc(root)} / ${this.esc(relpath)}</code><br><br>`
            + '磁盘文件与索引记录一并删除，检测结果保留，之后可以重新入库。',
            async () => {
                if (btn) { btn.disabled = true; btn.textContent = '删除中…'; }
                try {
                    await API.media.nsfwProbeUnimport(jobId, index);
                    const row = this._probeItems.find((entry) => Number(entry.index) === index);
                    if (row) delete row.imported;
                    this._renderProbeItems();
                    Toast.success(`已删除 ${root} / ${relpath}`);
                    await Promise.allSettled([this._loadStats(), this._loadList()]);
                } catch (err) {
                    Toast.error('删除失败：' + (err.message || err));
                    if (btn) { btn.disabled = false; btn.textContent = '删除'; }
                }
            },
        );
    },

    /** 入库 = 复制进媒体根 + 入队一次增量扫描；索引由扫描任务统一建，前端不假设已生效。 */
    async _importProbeItem(index, btn) {
        const jobId = this._probeJobId;
        const rootSel = document.getElementById('files-probe-root');
        const dirInput = document.getElementById('files-probe-dir');
        if (!jobId) return;
        if (!rootSel || !rootSel.value) {
            Toast.error('请先选择入库目标媒体根（没有可写根时无法入库）');
            return;
        }
        const rootId = rootSel.value;
        const targetDir = dirInput ? dirInput.value.trim() : '';
        if (btn) {
            btn.disabled = true;
            btn.textContent = '入库中…';
        }
        try {
            const data = await API.media.nsfwProbeImport(jobId, {
                index, root: rootId, target_dir: targetDir,
            });
            const item = this._probeItems.find((row) => Number(row.index) === index);
            if (item) item.imported = { root: data.root, relpath: data.relpath };
            this._renderProbeItems();
            Toast.success(data.scan_job_id
                ? `已入库：${data.root} / ${data.relpath}，已提交索引扫描`
                : `已入库：${data.root} / ${data.relpath}（索引队列已满，请稍后手动「扫描新增」）`);
            await Promise.allSettled([this._loadStats(), this._loadList()]);
        } catch (err) {
            Toast.error('入库失败：' + (err.message || err));
            if (btn) {
                btn.disabled = false;
                btn.textContent = '入库';
            }
        }
    },


    _openViewer(images, index, pano = false) {
        if (!this._viewer || !images || !images.length) return;
        // index 是传入集合的行号；用 root/relpath 定位，避免两处集合行号不一致时选错图。
        let imageIndex = Number(index) || 0;
        const row = images[imageIndex];
        if (row) {
            const found = images.findIndex((image) => image.root === row.root && image.relpath === row.relpath);
            if (found >= 0) imageIndex = found;
        }
        this._viewer.open(images, imageIndex, {
            // 查看器内可直接复核：图片行的 nsfw 结论来自后端有效判定，复核写 second 级结论。
            getNsfw: (image) => image.nsfw || null,
            review: async (image, label) => {
                const data = await API.media.nsfwReview({
                    root: image.root, relpath: image.relpath, label, reviewed_by: '本机',
                });
                image.nsfw = data.nsfw;
                await Promise.allSettled([this._loadStats(), this._loadList(), this._loadNsfw()]);
            },
        });
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
        const nsfw = document.getElementById('files-nsfw'); if (nsfw) nsfw.value = '';
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
                // 两个数字都指向下方的「重复内容组」：组数看有多少组，涉及文件看一共几个副本。
                body.innerHTML = `
                    <div class="files-stats-rows">
                        <button type="button" class="files-stat files-stat-link" data-jump="dupGroups" title="点击查看重复内容组">
                            <span class="files-stat-value">${this.esc(s.duplicate_groups)}</span>
                            <span class="files-stat-label">重复内容组</span>
                        </button>
                        <button type="button" class="files-stat files-stat-link" data-jump="dupGroups" title="点击查看重复内容组">
                            <span class="files-stat-value">${this.esc(s.duplicate_files)}</span>
                            <span class="files-stat-label">涉及文件</span>
                        </button>
                    </div>
                    <p class="form-hint">内容摘要相同即视为重复，跨媒体根也一并归组；点数字直达下方分组明细。</p>`;
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
                            <tr><th>预览</th><th>类型</th><th>媒体根</th><th>相对路径</th><th>大小</th><th>修改时间</th><th>操作</th></tr>
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
        // 预览：缩略图点开灯箱，同一摘要的所有副本都在里面，可以直接左右对比后再决定删哪个。
        const preview = member.kind === 'image'
            ? `<button type="button" class="files-dup-thumb" data-act="preview" data-sha="${this.esc(member.content_sha256 || '')}"
                    data-root="${this.esc(member.root)}" data-relpath="${this.esc(member.relpath)}" title="点开对比同内容副本">
                    <img class="files-thumb files-thumb-sm" src="${this.esc(this._thumbUrl(member))}" loading="lazy" alt="">
               </button>`
            : `<span class="files-thumb files-thumb-sm files-thumb-icon" title="${this.esc(this._kindLabel(member.kind))}">${this._kindIcon(member.kind)}</span>`;
        return `<tr>
            <td class="files-dup-preview">${preview}</td>
            <td>${this.esc(this._kindLabel(member.kind))}</td>
            <td>${this.esc(rootInfo.label)}</td>
            <td class="files-dup-path" title="${this.esc(member.relpath)}">${this.esc(member.relpath)}</td>
            <td>${this.esc(this._formatSize(member.size))}</td>
            <td>${this.esc(this._formatMtime(member.mtime))}</td>
            <td class="files-dup-op">${delBtn}</td>
        </tr>`;
    },

    /** 预览一个重复副本：灯箱里放的都是同一摘要的其它副本，方便对比后决定留哪个。 */
    _previewDup(sha, root, relpath) {
        const group = (this._dupGroups || []).find((item) => item.sha === sha);
        if (!group) return;
        const images = (group.members || []).filter((member) => member.kind === 'image');
        if (!images.length) return;
        const index = images.findIndex((member) => member.root === root && member.relpath === relpath);
        this._openViewer(images, index >= 0 ? index : 0);
    },

    async _onDupClick(event) {
        const act = event.target.closest('[data-act]');
        if (!act) return;
        if (act.dataset.act === 'delete') {
            this._confirmDelete(act.dataset.root, act.dataset.relpath);
            return;
        }
        if (act.dataset.act === 'preview') {
            this._previewDup(act.dataset.sha, act.dataset.root, act.dataset.relpath);
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
            // 并进 group.members：预览灯箱要能左右翻到这些展开后才加载到的副本。
            const merged = (group.members || []).slice();
            members.forEach((member) => {
                if (!merged.some((item) => item.root === member.root && item.relpath === member.relpath)) merged.push(member);
            });
            group.members = merged;
            more.innerHTML = '<table class="files-dup-table"><thead><tr>'
                + '<th>预览</th><th>类型</th><th>媒体根</th><th>相对路径</th><th>大小</th><th>修改时间</th><th>操作</th>'
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
