/**
 * 按号归档页面：先用 wmp 自己的采集能力把某个号最近 N 篇下载落盘，
 * 再触发媒体服务增量扫描入库。下载与入库是两个任务，进度分开显示。
 *
 * 兜底入口（该号不在 wmp 的账号池里）走媒体服务的 /media/api/archive/from-mp。
 */
const MediaArchivePage = {
    _accounts: [],
    _roots: [],
    _downloadTimer: null,
    _scanTimer: null,
    _archiveTimer: null,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">按号归档</h2>
                <p class="page-description">采集某个公众号最近几篇，落盘后自动增量入库。下载和入库是两个独立任务。</p>
            </div>

            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="card-header">
                    <h3 class="card-title">① 选择公众号与范围</h3>
                </div>

                <div class="grid grid-2" style="gap: 12px;">
                    <div class="form-group">
                        <label class="form-label" for="ma-account">公众号</label>
                        <select class="form-input" id="ma-account">
                            <option value="">正在读取公众号列表…</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="ma-limit">最近几篇</label>
                        <input type="number" class="form-input" id="ma-limit" value="10" min="1" max="50">
                        <div class="form-hint">已下载的会自动跳过，只取增量</div>
                    </div>
                </div>

                <div class="btn-group">
                    <button class="btn btn-primary" id="ma-start">📥 归档最近 N 篇</button>
                    <button class="btn btn-secondary" id="ma-scan-only">只扫描新增</button>
                </div>
                <div class="form-hint" style="margin-top: 8px;">
                    这些图会落进 wmp 的文章目录；媒体服务负责索引，检索页才能搜到。
                </div>
            </div>

            <div class="card" id="ma-progress-card" style="margin-bottom: var(--spacing-lg); display: none;">
                <div class="card-header">
                    <h3 class="card-title">② 任务进度</h3>
                </div>
                <div class="media-task">
                    <div class="media-task-head">
                        <span class="media-task-name">下载（wmp）</span>
                        <span class="media-task-state" id="ma-download-state">等待中</span>
                    </div>
                    <div class="media-task-detail" id="ma-download-detail"></div>
                </div>
                <div class="media-task">
                    <div class="media-task-head">
                        <span class="media-task-name">入库（媒体服务）</span>
                        <span class="media-task-state" id="ma-scan-state">未开始</span>
                    </div>
                    <div class="media-task-detail" id="ma-scan-detail"></div>
                </div>
            </div>

            <div class="card">
                <details class="media-advanced">
                    <summary>兜底：该号不在 wmp 账号池里</summary>
                    <div class="grid grid-2" style="gap: 12px; margin-top: 12px;">
                        <div class="form-group">
                            <label class="form-label" for="ma-fallback-fakeid">公众号 fakeid</label>
                            <input type="text" class="form-input" id="ma-fallback-fakeid" placeholder="例如 MzA5...">
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ma-fallback-count">抓取篇数</label>
                            <input type="number" class="form-input" id="ma-fallback-count" value="10" min="1" max="500">
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ma-root">目标媒体根</label>
                            <select class="form-input" id="ma-root"></select>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ma-target-dir">目标子目录（可空）</label>
                            <input type="text" class="form-input" id="ma-target-dir" placeholder="例如 汽车/某号">
                        </div>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-secondary" id="ma-fallback">交给媒体服务归档</button>
                    </div>
                    <div class="media-task-detail" id="ma-fallback-detail"></div>
                </details>
            </div>
        `;
    },

    async init() {
        this._downloadTimer = null;
        this._scanTimer = null;
        this._archiveTimer = null;

        document.getElementById('ma-start')?.addEventListener('click', () => this.startArchive());
        document.getElementById('ma-scan-only')?.addEventListener('click', () => {
            this.showProgress();
            this.runScan();
        });
        document.getElementById('ma-fallback')?.addEventListener('click', () => this.startFallback());

        await Promise.all([this.loadAccounts(), this.loadRoots()]);
    },

    destroy() {
        clearInterval(this._downloadTimer);
        clearInterval(this._scanTimer);
        clearInterval(this._archiveTimer);
        this._downloadTimer = null;
        this._scanTimer = null;
        this._archiveTimer = null;
    },

    // ── 数据加载 ─────────────────────────────────────
    async loadAccounts() {
        const select = document.getElementById('ma-account');
        if (!select) return;
        try {
            const data = await API.accounts.list();
            this._accounts = (data && data.accounts) || [];
        } catch (err) {
            select.innerHTML = '<option value="">公众号列表读取失败</option>';
            return;
        }
        if (!this._accounts.length) {
            select.innerHTML = '<option value="">账号池为空，请先到「公众号管理」添加</option>';
            return;
        }
        select.innerHTML = this._accounts.map((a) => {
            const name = a.nickname || a.name || a.fakeid;
            return `<option value="${this.esc(a.fakeid)}" data-name="${this.esc(name)}">${this.esc(name)}</option>`;
        }).join('');
    },

    async loadRoots() {
        const select = document.getElementById('ma-root');
        if (!select) return;
        try {
            const data = await API.media.roots();
            this._roots = (data && data.items) || [];
        } catch (err) {
            select.innerHTML = '<option value="wx-archive">wx-archive（媒体服务不可用）</option>';
            return;
        }
        const writable = this._roots.filter((r) => r.writable);
        select.innerHTML = writable.length
            ? writable.map((r) => `<option value="${this.esc(r.id)}">${this.esc(r.label || r.id)}</option>`).join('')
            : '<option value="wx-archive">wx-archive</option>';
    },

    // ── 归档流程 ─────────────────────────────────────
    async startArchive() {
        const select = document.getElementById('ma-account');
        const opt = select && select.selectedOptions[0];
        if (!opt || !opt.value) {
            Toast.error('请先选择公众号');
            return;
        }
        const limit = Number(document.getElementById('ma-limit')?.value || 10);
        if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
            Toast.error('篇数需在 1..50');
            return;
        }
        this.showProgress();
        this.setScanState('未开始', '');
        this.setDownloadState('提交中…', '');

        let started;
        try {
            started = await API.articles.collectLatest({
                fakeid: opt.value,
                account_name: opt.dataset.name || '',
                limit,
                skip_existing: true,
            });
        } catch (err) {
            this.setDownloadState('启动失败', err.message || '');
            Toast.error(`启动采集失败: ${err.message}`);
            return;
        }
        Toast.info(started.message || '已启动采集任务');
        this.pollDownload(started.task_id, limit);
    },

    pollDownload(taskId, limit) {
        clearInterval(this._downloadTimer);
        const tick = async () => {
            let task;
            try {
                task = await API.articles.downloadStatus(taskId);
            } catch (err) {
                clearInterval(this._downloadTimer);
                this._downloadTimer = null;
                this.setDownloadState('状态读取失败', err.message || '');
                return;
            }
            const total = task.total || limit;
            const done = (task.completed || 0) + (task.failed || 0);
            this.setDownloadState(
                task.status === 'completed' ? '已完成' : (task.status === 'failed' ? '失败' : '下载中'),
                `${done}/${total}　成功 ${task.completed || 0}　失败 ${task.failed || 0}${task.current ? '　' + task.current : ''}`,
            );
            if (['completed', 'failed'].includes(task.status)) {
                clearInterval(this._downloadTimer);
                this._downloadTimer = null;
                // 下载结束（即使有失败也仍可能落了图），照常触发增量扫描
                this.runScan();
            }
        };
        this._downloadTimer = setInterval(tick, 1200);
        tick();
    },

    async runScan() {
        clearInterval(this._scanTimer);
        this.setScanState('提交中…', '');
        let job;
        try {
            job = await API.media.scan('articles-full', false);
        } catch (err) {
            this.setScanState(`提交失败（${err.status || '网络'}）`, err.message || '');
            return;
        }
        const tick = async () => {
            let status;
            try {
                status = await API.media.scanStatus(job.id);
            } catch (err) {
                clearInterval(this._scanTimer);
                this._scanTimer = null;
                this.setScanState('状态读取失败', err.message || '');
                return;
            }
            this.setScanState(
                status.status === 'completed' ? '已完成' : (status.status === 'failed' ? '失败' : '入库中'),
                `已扫 ${status.scanned || 0}　新增 ${status.indexed_new || 0}　更新 ${status.indexed_updated || 0}　失败 ${status.failed || 0}${status.current ? '　' + status.current : ''}`,
            );
            if (['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(status.status)) {
                clearInterval(this._scanTimer);
                this._scanTimer = null;
            }
        };
        this._scanTimer = setInterval(tick, 1200);
        tick();
    },

    async startFallback() {
        const fakeid = (document.getElementById('ma-fallback-fakeid')?.value || '').trim();
        if (!fakeid) {
            Toast.error('请输入 fakeid');
            return;
        }
        const count = Number(document.getElementById('ma-fallback-count')?.value || 10);
        const detail = document.getElementById('ma-fallback-detail');
        if (detail) detail.textContent = '正在提交…';
        let job;
        try {
            job = await API.media.archiveFromMp({
                fakeid,
                count,
                target_root: document.getElementById('ma-root')?.value || 'wx-archive',
                target_dir: (document.getElementById('ma-target-dir')?.value || '').trim(),
            });
        } catch (err) {
            if (detail) detail.textContent = `提交失败：${err.message}`;
            return;
        }
        Toast.info('媒体服务已接单');
        this.pollArchiveJob(job.id);
    },

    pollArchiveJob(jobId) {
        clearInterval(this._archiveTimer);
        const tick = async () => {
            const detail = document.getElementById('ma-fallback-detail');
            let job;
            try {
                job = await API.media.archiveStatus(jobId);
            } catch (err) {
                clearInterval(this._archiveTimer);
                this._archiveTimer = null;
                if (detail) detail.textContent = `状态读取失败：${err.message}`;
                return;
            }
            if (detail) {
                detail.textContent = `状态 ${job.status}　已归档 ${job.saved || 0}　失败 ${job.failed || 0}${job.message ? '　' + job.message : ''}`;
            }
            if (['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(job.status)) {
                clearInterval(this._archiveTimer);
                this._archiveTimer = null;
                if (job.status === 'completed') this.runScan();
            }
        };
        this._archiveTimer = setInterval(tick, 1200);
        tick();
    },

    // ── UI 小工具 ────────────────────────────────────
    showProgress() {
        const card = document.getElementById('ma-progress-card');
        if (card) card.style.display = 'block';
    },

    setDownloadState(state, detail) {
        const s = document.getElementById('ma-download-state');
        const d = document.getElementById('ma-download-detail');
        if (s) s.textContent = state;
        if (d) d.textContent = detail || '';
    },

    setScanState(state, detail) {
        const s = document.getElementById('ma-scan-state');
        const d = document.getElementById('ma-scan-detail');
        if (s) s.textContent = state;
        if (d) d.textContent = detail || '';
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
