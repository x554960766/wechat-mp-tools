/**
 * 按号归档页面：「按账号」分段选平台账号，用该平台的原生采集能力把最近 N 篇下载落盘，
 * 下载终态后由后端适配层自动触发媒体服务增量扫描入库（进度在「任务」卡里分两行显示）。
 *
 * 平台/账号从宿主适配层 /api/archive 读取；平台能力（是否支持贴链接、范围单位）
 * 由 /api/archive/platforms 返回，前端不写死平台名单。
 */
const MediaArchivePage = {
    _accounts: [],
    _platforms: [],
    _roots: [],
    _downloadTimer: null,
    _scanTimer: null,
    _archiveTimer: null,
    _nasTimer: null,
    _jobsTimer: null,
    _exportTimer: null,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">按号归档</h2>
                <p class="page-description">
                    按平台选号采集最近 N 篇并自动增量入库；也可以把已下载作品登记归档，或直接粘贴链接建任务。
                    下载、入库、归档是三个独立任务，进度在下方「任务」里分开显示。
                </p>
            </div>

            <div class="card ma-create-card">
                <div class="card-header">
                    <h3 class="card-title">新建归档任务</h3>
                    <div class="ma-mode-tabs" id="ma-mode-tabs" role="tablist">
                        <button type="button" class="ma-mode-tab is-active" data-mode="account" role="tab" aria-selected="true">按账号</button>
                        <button type="button" class="ma-mode-tab" data-mode="platform" role="tab" aria-selected="false">登记已下载</button>
                        <button type="button" class="ma-mode-tab" data-mode="link" role="tab" aria-selected="false">粘贴链接</button>
                    </div>
                </div>

                <div class="ma-panel" data-arch-mode="account">
                    <div class="grid grid-2" style="gap: 12px;">
                        <div class="form-group">
                            <label class="form-label" for="ma-platform-select">平台</label>
                            <select class="form-input" id="ma-platform-select">
                                <option value="">正在读取平台…</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ma-account">账号</label>
                            <select class="form-input" id="ma-account">
                                <option value="">正在读取账号列表…</option>
                            </select>
                        </div>
                    </div>

                    <div class="grid grid-2" style="gap: 12px;">
                        <div class="form-group">
                            <label class="form-label" for="ma-limit" id="ma-limit-label">最近几篇</label>
                            <input type="number" class="form-input" id="ma-limit" value="10" min="1" max="50">
                            <div class="form-hint" id="ma-limit-hint">已下载的会自动跳过，只取增量</div>
                        </div>
                        <div class="form-group" id="ma-link-group" style="display: none;">
                            <label class="form-label" for="ma-link">或贴作者主页链接</label>
                            <input type="text" class="form-input" id="ma-link" placeholder="账号还没收藏时用这个">
                            <div class="form-hint">该账号不在收藏列表里时，贴主页链接也能归档</div>
                        </div>
                    </div>

                    <div class="btn-group">
                        <button class="btn btn-primary" id="ma-start">📥 归档最近 N 篇</button>
                        <button class="btn btn-secondary" id="ma-scan-only">只扫描新增</button>
                    </div>
                    <div class="form-hint" style="margin-top: 8px;" id="ma-platform-note">
                        这些作品会落进该平台在 wmp 的下载目录；媒体服务负责索引，检索页才能搜到。
                    </div>
                </div>

                <div class="ma-panel" data-arch-mode="platform" hidden>
                    <div class="grid grid-2" style="gap: 12px;">
                        <div class="form-group">
                            <label class="form-label" for="ma-platform">平台</label>
                            <select class="form-input" id="ma-platform">
                                <option value="wechat">微信公众号</option>
                                <option value="channels">微信视频号</option>
                                <option value="douyin">抖音</option>
                                <option value="kuaishou">快手</option>
                                <option value="bilibili">B站</option>
                                <option value="xhs">小红书</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ma-platform-account">账号（可空）</label>
                            <input type="text" class="form-input" id="ma-platform-account" placeholder="留空 = 该平台全部">
                            <div class="form-hint">抖音/快手填作者名，公众号填公众号名；留空则登记该平台全部已下载作品</div>
                        </div>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-primary" id="ma-platform-archive">📥 登记该平台已下载作品</button>
                    </div>
                    <div class="media-task-detail" id="ma-platform-detail" style="margin-top: 8px;"></div>
                </div>

                <div class="ma-panel" data-arch-mode="link" hidden>
                    <div class="form-group">
                        <label class="form-label" for="ma-batch-text">粘贴链接</label>
                        <textarea class="form-input" id="ma-batch-text" rows="4"
                            placeholder="https://mp.weixin.qq.com/s?...（一行一条、或用整段分享文案都行）"></textarea>
                        <div class="form-hint">从文本里自动抽公众号文章链接，按 __biz/mid/idx 去重；单任务上限 500 条</div>
                    </div>

                    <div class="grid grid-2" style="gap: 12px;">
                        <div class="form-group">
                            <label class="form-label" for="ma-batch-file">或上传 txt / csv / zip</label>
                            <input type="file" class="form-input" id="ma-batch-file" accept=".txt,.csv,.zip">
                            <div class="form-hint">zip 只读里面的 txt/csv，其他条目忽略</div>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="ma-batch-root">目标媒体根</label>
                            <select class="form-input" id="ma-batch-root"></select>
                            <div class="form-hint">文章落在该根下的目标子目录里</div>
                        </div>
                    </div>

                    <div class="form-group">
                        <label class="form-label" for="ma-batch-dir">目标子目录（可空）</label>
                        <input type="text" class="form-input" id="ma-batch-dir" placeholder="例如 汽车/某号">
                    </div>

                    <div class="btn-group">
                        <button class="btn btn-primary" id="ma-batch-create">📥 用粘贴的链接建任务</button>
                        <button class="btn btn-secondary" id="ma-batch-upload">📄 上传文件建任务</button>
                    </div>
                    <div class="media-task-detail" id="ma-batch-detail"></div>
                </div>

                <details class="media-advanced ma-fallback">
                    <summary>兜底：该号不在 wmp 账号池里（fakeid 直接归档）</summary>
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

            <div class="card" id="ma-task-card">
                <div class="card-header">
                    <h3 class="card-title">任务</h3>
                    <div class="ma-task-tools">
                        <span class="media-task-state" id="ma-jobs-summary">读取中…</span>
                        <button class="btn btn-secondary btn-sm" id="ma-nas-toggle" type="button"
                                aria-expanded="false" title="各平台下载成功后自动往 NAS 归档一份同版本文件">
                            NAS 自动归档
                        </button>
                    </div>
                </div>

                <div id="ma-progress-card" style="display: none;">
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

                <div class="ma-nas-block" id="ma-nas-block" hidden>
                    <div class="ma-nas-head">
                        <span class="media-task-name">NAS 自动归档</span>
                        <span class="media-task-state" id="ma-nas-summary">读取中…</span>
                    </div>
                    <div class="form-hint" style="margin-bottom: 10px;">
                        各平台下载成功后自动往 NAS 归档一份同版本文件（保留本地，供检索与离线打开）。归档只是多一份副本，不会删本地。
                    </div>
                    <div id="ma-nas-list"></div>
                </div>

                <div class="form-hint" style="margin-bottom: 10px;">
                    等验证的任务可以开验证窗口（有头浏览器 180 秒）；窗口结束后任务会转「已中断」，
                    在窗口里验证通过后点「恢复任务」才会重跑——系统不推断验证成功。
                </div>
                <div id="ma-jobs-list"></div>
            </div>
        `;
    },

    async init() {
        this._downloadTimer = null;
        this._scanTimer = null;
        this._archiveTimer = null;
        this._nasTimer = null;
        this._jobsTimer = null;
        this._exportTimer = null;

        document.getElementById('ma-start')?.addEventListener('click', () => this.startArchive());
        document.getElementById('ma-platform-select')?.addEventListener('change', () => this.onPlatformChange());
        document.getElementById('ma-nas-list')?.addEventListener('click', (ev) => {
            const btn = ev.target.closest('[data-nas-retry]');
            if (btn) this.retryNasArchive(btn.getAttribute('data-nas-retry'));
        });
        document.getElementById('ma-scan-only')?.addEventListener('click', () => {
            this.showProgress();
            this.runScan();
        });
        document.getElementById('ma-platform-archive')?.addEventListener('click', () => {
            this.archivePlatformWorks();
        });
        document.getElementById('ma-fallback')?.addEventListener('click', () => this.startFallback());
        // 三种建任务方式合成一张卡：分段切换只换表单，不换页面
        document.getElementById('ma-mode-tabs')?.addEventListener('click', (ev) => {
            const btn = ev.target.closest('[data-mode]');
            if (btn) this.setArchMode(btn.dataset.mode);
        });
        // NAS 不再占一张卡：缩成任务卡里的一个开关
        document.getElementById('ma-nas-toggle')?.addEventListener('click', (ev) => {
            const block = document.getElementById('ma-nas-block');
            if (!block) return;
            block.hidden = !block.hidden;
            ev.currentTarget.setAttribute('aria-expanded', String(!block.hidden));
        });

        document.getElementById('ma-batch-create')?.addEventListener('click', () => this.createBatchJob());
        document.getElementById('ma-batch-upload')?.addEventListener('click', () => this.uploadBatchFile());
        document.getElementById('ma-jobs-list')?.addEventListener('click', (ev) => {
            const verify = ev.target.closest('[data-verify-job]');
            if (verify) {
                this.startVerify(verify.getAttribute('data-verify-job'),
                    Number(verify.getAttribute('data-verify-index')));
                return;
            }
            const exportBtn = ev.target.closest('[data-export-job]');
            if (exportBtn) {
                this.exportZip(exportBtn.getAttribute('data-export-job'), exportBtn);
                return;
            }
            const retry = ev.target.closest('[data-retry-job]');
            if (retry) this.retryJob(retry.getAttribute('data-retry-job'));
        });

        await Promise.all([this.loadPlatforms(), this.loadRoots()]);
        this.startNasPolling();
        this.startJobsPolling();
    },

    destroy() {
        clearInterval(this._downloadTimer);
        clearInterval(this._scanTimer);
        clearInterval(this._archiveTimer);
        clearInterval(this._nasTimer);
        clearInterval(this._jobsTimer);
        clearInterval(this._exportTimer);
        this._downloadTimer = null;
        this._scanTimer = null;
        this._archiveTimer = null;
        this._nasTimer = null;
        this._jobsTimer = null;
        this._exportTimer = null;
    },

    // ── 数据加载 ─────────────────────────────────────
    async loadPlatforms() {
        const select = document.getElementById('ma-platform-select');
        if (!select) return;
        try {
            const data = await API.archive.platforms();
            this._platforms = (data && data.platforms) || [];
        } catch (err) {
            // 带上具体原因（常见：服务还是旧进程，/api/archive 尚未注册 → HTTP 404）
            select.innerHTML = `<option value="">平台列表读取失败：${this.esc(err.message || '')}</option>`;
            return;
        }
        const enabled = this._platforms.filter((p) => p.enabled !== false);
        if (!enabled.length) {
            select.innerHTML = '<option value="">没有可用平台</option>';
            return;
        }
        select.innerHTML = enabled
            .map((p) => `<option value="${this.esc(p.id)}">${this.esc(p.name || p.id)}</option>`)
            .join('');
        await this.onPlatformChange();
    },

    currentPlatform() {
        const id = document.getElementById('ma-platform-select')?.value || '';
        return this._platforms.find((p) => p.id === id) || null;
    },

    // 平台切换：范围单位、贴链接入口、说明文案、账号列表都跟着换
    async onPlatformChange() {
        const platform = this.currentPlatform();
        const limitLabel = document.getElementById('ma-limit-label');
        const limitHint = document.getElementById('ma-limit-hint');
        const note = document.getElementById('ma-platform-note');
        const linkGroup = document.getElementById('ma-link-group');
        const startBtn = document.getElementById('ma-start');
        if (platform) {
            const byPage = platform.limit_unit === 'page';
            if (limitLabel) limitLabel.textContent = byPage ? '最近几页' : '最近几篇';
            if (startBtn) startBtn.textContent = byPage ? '📥 归档最近 N 页' : '📥 归档最近 N 篇';
            if (limitHint) {
                limitHint.textContent = byPage
                    ? (platform.note || '按页下载，实际条数可能多于设置值')
                    : '已下载的会自动跳过，只取增量';
            }
            if (note) {
                note.textContent = platform.note
                    ? `${platform.note}。下载落到媒体根 ${platform.root}，随后由媒体服务索引。`
                    : `下载落到媒体根 ${platform.root}，随后由媒体服务索引。`;
            }
            if (linkGroup) linkGroup.style.display = platform.allow_link ? '' : 'none';
        }
        await this.loadAccounts(platform ? platform.id : '');
    },

    async loadAccounts(platform) {
        const select = document.getElementById('ma-account');
        if (!select) return;
        this._accounts = [];
        if (!platform) {
            select.innerHTML = '<option value="">先选择平台</option>';
            return;
        }
        select.innerHTML = '<option value="">正在读取…</option>';
        try {
            const data = await API.archive.accounts(platform);
            this._accounts = (data && data.accounts) || [];
        } catch (err) {
            select.innerHTML = `<option value="">账号列表读取失败：${this.esc(err.message || '')}</option>`;
            return;
        }
        if (!this._accounts.length) {
            const page = {
                wechat: '「公众号管理」',
                xhs: '「小红书」',
                bilibili: '「B站」',
                kuaishou: '「快手」',
            }[platform] || '对应平台页';
            select.innerHTML = `<option value="">还没有收藏的账号，请先到${page}添加</option>`;
            return;
        }
        select.innerHTML = this._accounts.map((a) => {
            const name = a.name || a.id;
            return `<option value="${this.esc(a.id)}" data-name="${this.esc(name)}" data-url="${this.esc(a.url || '')}">${this.esc(name)}</option>`;
        }).join('');
    },

    async loadRoots() {
        // 两个入口（兜底 from-mp、「粘贴链接」分段）共用同一份可写根列表
        const selects = ['ma-root', 'ma-batch-root']
            .map((id) => document.getElementById(id))
            .filter(Boolean);
        if (!selects.length) return;
        const fill = (html) => selects.forEach((el) => { el.innerHTML = html; });
        try {
            const data = await API.media.roots();
            this._roots = (data && data.items) || [];
        } catch (err) {
            fill('<option value="wx-archive">wx-archive（媒体服务不可用）</option>');
            return;
        }
        const writable = this._roots.filter((r) => r.writable);
        fill(writable.length
            ? writable.map((r) => `<option value="${this.esc(r.id)}">${this.esc(r.label || r.id)}</option>`).join('')
            : '<option value="wx-archive">wx-archive</option>');
    },

    // ── 归档流程 ─────────────────────────────────────
    async startArchive() {
        const platform = this.currentPlatform();
        if (!platform) {
            Toast.error('请先选择平台');
            return;
        }
        const select = document.getElementById('ma-account');
        const opt = select && select.selectedOptions[0];
        const accountId = opt && opt.value ? opt.value : '';
        const link = (document.getElementById('ma-link')?.value || '').trim();
        if (!accountId && !(platform.allow_link && link)) {
            Toast.error(platform.allow_link ? '请选择账号，或贴作者主页链接' : '请先选择账号');
            return;
        }
        const limit = Number(document.getElementById('ma-limit')?.value || 10);
        if (!Number.isInteger(limit) || limit < 1 || limit > 50) {
            Toast.error('数量需在 1..50');
            return;
        }
        this.showProgress();
        this.setScanState('未开始', '');
        this.setDownloadState('提交中…', '');

        let started;
        try {
            started = await API.archive.start({
                platform: platform.id,
                account_id: accountId,
                account_name: accountId ? (opt.dataset.name || '') : link,
                limit,
                profile_url: platform.allow_link ? link : '',
            });
        } catch (err) {
            this.setDownloadState('启动失败', err.message || '');
            Toast.error(`启动失败: ${err.message}`);
            return;
        }
        Toast.info(`已提交${platform.name || platform.id}归档任务`);
        this.pollArchive(started.task_id, platform.id);
    },

    pollArchive(taskId, platform) {
        clearInterval(this._downloadTimer);
        const tick = async () => {
            let task;
            try {
                task = await API.archive.status(platform, taskId);
            } catch (err) {
                clearInterval(this._downloadTimer);
                this._downloadTimer = null;
                this.setDownloadState('状态读取失败', err.message || '');
                return;
            }
            const total = task.total || task.requested || 0;
            const done = (task.completed || 0) + (task.failed || 0);
            const label = {
                idle: '启动中',
                running: '下载中',
                completed: '已完成',
                failed: '失败',
                cancelled: '已取消',
            }[task.status] || task.status;
            this.setDownloadState(
                label,
                `${done}/${total}　成功 ${task.completed || 0}　失败 ${task.failed || 0}`
                    + `${task.skipped ? `　跳过 ${task.skipped}` : ''}${task.current ? '　' + task.current : ''}`,
            );
            if (['completed', 'failed', 'cancelled'].includes(task.status)) {
                clearInterval(this._downloadTimer);
                this._downloadTimer = null;
                // 后端在下载终态时已经提交过该媒体根的扫描，前端只负责看结果
                if (task.scan_job_id) {
                    this.pollScan(task.scan_job_id);
                } else if (task.scan_error) {
                    this.setScanState('未入库', task.scan_error);
                } else {
                    this.setScanState('未开始', '下载已结束，但没有提交扫描');
                }
            }
        };
        this._downloadTimer = setInterval(tick, 1200);
        tick();
    },

    pollScan(jobId) {
        clearInterval(this._scanTimer);
        this.setScanState('入库中', '已提交扫描，等待媒体服务…');
        const tick = async () => {
            let status;
            try {
                status = await API.media.scanStatus(jobId);
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

    scanRoot() {
        const platform = this.currentPlatform();
        return (platform && platform.root) || 'articles-full';
    },

    async runScan() {
        clearInterval(this._scanTimer);
        const root = this.scanRoot();
        this.setScanState('提交中…', `媒体根 ${root}`);
        let job;
        try {
            job = await API.media.scan(root, false);
        } catch (err) {
            this.setScanState(`提交失败（${err.status || '网络'}）`, err.message || '');
            return;
        }
        this.pollScan(job.id);
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
                const counts = this.jobCounts(job);
                const label = this._jobStatusLabels[job.status] || job.status;
                detail.textContent = `状态 ${label}　链接 ${counts.total} 条　成功 ${counts.done}　失败 ${counts.failed}`
                    + `${counts.pendingVerify.length ? `　待验证 ${counts.pendingVerify.length}（在下方任务表里开验证窗口）` : ''}`
                    + `${job.message ? '　' + job.message : ''}`;
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

    // ── 手动多平台归档登记 ──────────────────────────
    /**
     * 把所选平台历史清单里已下载的作品批量登记进 NAS 归档队列。
     * 只登记入队，实际复制由后台队列完成，所以这里只回填汇总结果并刷新归档状态卡。
     */
    async archivePlatformWorks() {
        const detail = document.getElementById('ma-platform-detail');
        const platform = document.getElementById('ma-platform')?.value || '';
        const account = (document.getElementById('ma-platform-account')?.value || '').trim();
        if (!platform) {
            Toast.error('请选择平台');
            return;
        }
        if (detail) detail.textContent = '正在登记…';
        try {
            const data = await API.media.nasArchivePlatform(platform, account);
            const parts = [`平台 ${data.platform_label || platform}`];
            if (account) parts.push(`账号 ${account}`);
            parts.push(`新登记 ${data.queued || 0} 条`);
            if (data.skipped) parts.push(`已存在跳过 ${data.skipped} 条`);
            const failed = data.failed || [];
            if (failed.length) parts.push(`失败 ${failed.length} 条`);
            if (detail) detail.textContent = parts.join('　');
            if (failed.length) {
                Toast.warning(`已登记 ${data.queued || 0} 条，${failed.length} 条登记失败`);
            } else {
                Toast.success(`已登记 ${data.queued || 0} 条作品，后台开始归档`);
            }
            this.loadNasArchive();
        } catch (err) {
            if (detail) detail.textContent = `登记失败：${err.message}`;
            Toast.error(`登记失败：${err.message}`);
        }
    },

    // ── NAS 自动归档状态 ─────────────────────────────
    startNasPolling() {
        clearInterval(this._nasTimer);
        this.loadNasArchive();
        this._nasTimer = setInterval(() => this.loadNasArchive(), 5000);
    },

    async loadNasArchive() {
        const summary = document.getElementById('ma-nas-summary');
        let data;
        try {
            data = await API.media.nasStatus();
        } catch (err) {
            if (summary) summary.textContent = '状态读取失败';
            return;
        }
        if (!data) return;
        if (summary) {
            const counts = data.counts || {};
            const parts = [];
            if (!data.enabled) parts.push('已关闭');
            if (counts.queued) parts.push(`待归档 ${counts.queued}`);
            if (counts.submitted) parts.push(`归档中 ${counts.submitted}`);
            if (counts.archived) parts.push(`已归档 ${counts.archived}`);
            if (counts.failed) parts.push(`失败 ${counts.failed}`);
            summary.textContent = parts.length ? parts.join('　') : '暂无归档任务';
        }
        this.renderNasList(data.recent || []);
    },

    renderNasList(items) {
        const box = document.getElementById('ma-nas-list');
        if (!box) return;
        if (!items.length) {
            box.innerHTML = '<div class="media-task-detail">还没有作品走过自动归档。任意平台下载成功后会自动出现在这里。</div>';
            return;
        }
        const labels = { queued: '待归档', submitted: '归档中', archived: '已归档', failed: '失败' };
        box.innerHTML = items.map((item) => {
            const status = labels[item.status] || item.status || '未知';
            const bits = [];
            if (item.attempts) bits.push(`尝试 ${item.attempts} 次`);
            if (item.job_id) bits.push(`任务 ${String(item.job_id).slice(0, 8)}`);
            if (item.error) bits.push(this.esc(item.error));
            const retry = item.status === 'failed'
                ? `<button class="btn btn-secondary" style="padding:2px 10px;font-size:12px;" data-nas-retry="${this.esc(item.work_key)}">重试</button>`
                : '';
            return `
                <div class="media-task">
                    <div class="media-task-head">
                        <span class="media-task-name">${this.esc(item.work_key)}</span>
                        <span class="media-task-state">${this.esc(status)}</span>
                    </div>
                    <div class="media-task-detail">${bits.length ? bits.join('　') : '&nbsp;'}</div>
                    ${retry ? `<div class="btn-group" style="margin-top:6px;">${retry}</div>` : ''}
                </div>`;
        }).join('');
    },

    async retryNasArchive(workKey) {
        if (!workKey) return;
        try {
            await API.media.nasRetry(workKey);
            Toast.info('已重新排队');
        } catch (err) {
            Toast.error(`重试失败: ${err.message}`);
        }
        this.loadNasArchive();
    },

    // ── 链接批量归档与归档任务（媒体服务 /api/archive/jobs）────
    _jobStatusLabels: {
        queued: '排队中',
        running: '归档中',
        waiting_verification: '等待验证',
        cancelling: '取消中',
        cancelled: '已取消',
        completed: '已完成',
        partial: '部分完成',
        failed: '失败',
        interrupted: '已中断',
    },

    batchTarget() {
        return {
            target_root: document.getElementById('ma-batch-root')?.value || 'wx-archive',
            target_dir: (document.getElementById('ma-batch-dir')?.value || '').trim(),
        };
    },

    setBatchDetail(text) {
        const box = document.getElementById('ma-batch-detail');
        if (box) box.textContent = text || '';
    },

    async createBatchJob() {
        const text = (document.getElementById('ma-batch-text')?.value || '').trim();
        if (!text) {
            Toast.error('先粘贴至少一条文章链接');
            return;
        }
        this.setBatchDetail('正在建任务…');
        try {
            const job = await API.media.archiveCreate({ text, ...this.batchTarget() });
            this.setBatchDetail(`已建任务 ${String(job.id).slice(0, 8)}：${this.esc(job.status)}`);
            Toast.success('归档任务已接单');
            document.getElementById('ma-batch-text').value = '';
        } catch (err) {
            this.setBatchDetail(`建任务失败：${err.message || ''}`);
        }
        this.refreshJobs();
    },

    async uploadBatchFile() {
        const input = document.getElementById('ma-batch-file');
        const file = input && input.files && input.files[0];
        if (!file) {
            Toast.error('先选一个 txt / csv / zip');
            return;
        }
        const fd = new FormData();
        fd.append('file', file);
        const target = this.batchTarget();
        fd.append('target_root', target.target_root);
        fd.append('target_dir', target.target_dir);
        this.setBatchDetail(`正在上传 ${file.name}…`);
        try {
            const job = await API.media.archiveUpload(fd);
            this.setBatchDetail(`已建任务 ${String(job.id).slice(0, 8)}：${this.esc(job.status)}`);
            Toast.success(`${file.name} 已提交`);
            input.value = '';
        } catch (err) {
            this.setBatchDetail(`上传失败：${err.message || ''}`);
        }
        this.refreshJobs();
    },

    // ── 归档任务列表：验证窗口、导出 ZIP、恢复 ────────
    startJobsPolling() {
        clearInterval(this._jobsTimer);
        this.refreshJobs();
        this._jobsTimer = setInterval(() => this.refreshJobs(), 4000);
    },

    async refreshJobs() {
        const box = document.getElementById('ma-jobs-list');
        if (!box) return;
        let data;
        try {
            data = await API.media.archiveJobs(6);
        } catch (err) {
            box.innerHTML = `<div class="media-task-detail">任务列表读取失败：${this.esc(err.message || '')}</div>`;
            const summary = document.getElementById('ma-jobs-summary');
            if (summary) summary.textContent = '读取失败';
            return;
        }
        const items = (data && data.items) || [];
        const summary = document.getElementById('ma-jobs-summary');
        if (summary) {
            const active = items.filter((j) => ['queued', 'running', 'waiting_verification', 'cancelling']
                .includes(j.status)).length;
            summary.textContent = items.length
                ? `共 ${data.total || items.length} 个${active ? `　进行中 ${active}` : ''}`
                : '暂无归档任务';
        }
        this.renderJobs(items);
    },

    /** 任务计数以 articles 为准：后端的 saved/failed 只在部分 runner 上存在 */
    jobCounts(job) {
        const articles = (job && job.articles) || [];
        const total = (job && job.urls && job.urls.length) || articles.length;
        const done = articles.filter((a) => a.status === 'completed').length;
        const failed = articles.filter((a) => ['failed', 'partial'].includes(a.status)).length;
        const pendingVerify = [];
        articles.forEach((a, index) => {
            if (a.status === 'verify') pendingVerify.push(index);
        });
        return { total, done, failed, pendingVerify };
    },

    renderJobs(items) {
        const box = document.getElementById('ma-jobs-list');
        if (!box) return;
        if (!items.length) {
            box.innerHTML = '<div class="media-task-detail">还没有归档任务。可以在上方「粘贴链接」建一个，或用「按账号」归档最近 N 篇。</div>';
            return;
        }
        box.innerHTML = items.map((job) => {
            const counts = this.jobCounts(job);
            const label = this._jobStatusLabels[job.status] || job.status || '未知';
            const bits = [`链接 ${counts.total} 条`];
            if (counts.done) bits.push(`成功 ${counts.done}`);
            if (counts.failed) bits.push(`失败 ${counts.failed}`);
            if (counts.pendingVerify.length) bits.push(`待验证 ${counts.pendingVerify.length}`);
            if (job.message) bits.push(this.esc(job.message));
            if (job.errors && job.errors.length) bits.push(this.esc(String(job.errors[0])));

            const actions = [];
            if (counts.pendingVerify.length) {
                actions.push(`<button class="btn btn-primary" style="padding:2px 10px;font-size:12px;"`
                    + ` data-verify-job="${this.esc(job.id)}" data-verify-index="${counts.pendingVerify[0]}">`
                    + '🔓 打开验证窗口</button>');
            }
            if (['completed', 'partial', 'interrupted'].includes(job.status)) {
                actions.push(`<button class="btn btn-secondary" style="padding:2px 10px;font-size:12px;"`
                    + ` data-export-job="${this.esc(job.id)}">📦 导出 ZIP</button>`);
            }
            if (['interrupted', 'partial', 'failed', 'cancelled'].includes(job.status)) {
                actions.push(`<button class="btn btn-secondary" style="padding:2px 10px;font-size:12px;"`
                    + ` data-retry-job="${this.esc(job.id)}">↻ 恢复任务</button>`);
            }
            const title = `${this.esc(String(job.id).slice(0, 8))}　${this.esc(job.target_dir || job.target_root || '')}`;
            return `
                <div class="media-task">
                    <div class="media-task-head">
                        <span class="media-task-name">${title}</span>
                        <span class="media-task-state">${this.esc(label)}</span>
                    </div>
                    <div class="media-task-detail">${bits.join('　')}</div>
                    ${actions.length ? `<div class="btn-group" style="margin-top:6px;">${actions.join('')}</div>` : ''}
                </div>`;
        }).join('');
    },

    async startVerify(jobId, index) {
        try {
            const ctrl = await API.media.archiveVerify(jobId, index);
            const seconds = ctrl.expires_seconds || 180;
            Toast.info(`验证窗口已打开，${seconds} 秒内到浏览器里完成验证；窗口结束点「恢复任务」继续`);
        } catch (err) {
            // 409（已有验证窗口在跑）等已由 API 层提示
        }
        this.refreshJobs();
    },

    async retryJob(jobId) {
        try {
            const item = await API.media.archiveRetry(jobId);
            Toast.success(`已恢复，新任务 ${String(item.id).slice(0, 8)}`);
        } catch (err) {
            // API 层已提示
        }
        this.refreshJobs();
    },

    async exportZip(jobId, btn) {
        if (btn) btn.disabled = true;
        try {
            const settled = await this.tryFetchZip(jobId);
            if (!settled) Toast.info('正在打包 ZIP，完成后自动下载');
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    /**
     * 导出 ZIP 的三种响应：200 直接是文件、202 回 export_id（打包中）、409 还没到可导出状态。
     * 所以这里自己 fetch 判 content-type，不能走 API.request（它只认 JSON）。
     */
    async tryFetchZip(jobId) {
        let resp;
        try {
            resp = await fetch(API.media.archiveDownloadUrl(jobId));
        } catch (err) {
            Toast.error('下载请求失败，请检查服务是否运行');
            return true;
        }
        const type = resp.headers.get('content-type') || '';
        if (resp.ok && !type.includes('json')) {
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ai-media-${jobId}.zip`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 10000);
            Toast.success('ZIP 已开始下载');
            return true;
        }
        let data = {};
        try {
            data = await resp.json();
        } catch (err) {
            data = {};
        }
        if (resp.status === 409) {
            Toast.error(data.error || '归档尚未就绪');
            return true;
        }
        if (data.export_id) {
            this.pollExport(data.export_id, jobId);
            return false;
        }
        Toast.error(data.error || `导出失败（HTTP ${resp.status}）`);
        return true;
    },

    pollExport(exportId, jobId) {
        clearInterval(this._exportTimer);
        const tick = async () => {
            let status;
            try {
                status = await API.media.archiveExportStatus(exportId);
            } catch (err) {
                clearInterval(this._exportTimer);
                this._exportTimer = null;
                return;
            }
            if (status.status === 'completed') {
                clearInterval(this._exportTimer);
                this._exportTimer = null;
                this.tryFetchZip(jobId);
            } else if (status.status === 'failed') {
                clearInterval(this._exportTimer);
                this._exportTimer = null;
                Toast.error(`打包失败：${status.error || '未知原因'}`);
            }
        };
        this._exportTimer = setInterval(tick, 1500);
        tick();
    },

    // ── UI 小工具 ────────────────────────────────────
    /** 切换建任务方式：按账号 / 登记已下载 / 粘贴链接，同一块卡里换表单。 */
    setArchMode(mode) {
        const tabs = document.querySelectorAll('#ma-mode-tabs [data-mode]');
        tabs.forEach((btn) => {
            const active = btn.dataset.mode === mode;
            btn.classList.toggle('is-active', active);
            btn.setAttribute('aria-selected', String(active));
        });
        document.querySelectorAll('[data-arch-mode]').forEach((panel) => {
            panel.hidden = panel.dataset.archMode !== mode;
        });
    },

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
