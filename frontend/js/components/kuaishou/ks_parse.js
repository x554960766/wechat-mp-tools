const KsParsePage = {
    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">解析与下载</h2>
                <p class="page-description">粘贴快手作品链接或用户主页链接进行下载（用户主页批量需先扫码登录）</p>
            </div>

            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="form-group">
                    <label class="form-label">链接类型</label>
                    <div style="display: flex; gap: var(--spacing-md); margin-bottom: var(--spacing-md);">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="radio" name="ks-parse-type" value="single" checked onchange="KsParsePage.toggleType()"> 单个视频/图集
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="radio" name="ks-parse-type" value="profile" onchange="KsParsePage.toggleType()"> 用户主页批量下载
                        </label>
                    </div>
                </div>

                <div class="form-group">
                    <label class="form-label">快手链接</label>
                    <div style="display: flex; gap: var(--spacing-md);">
                        <input type="text" id="ks-url-input" class="form-input" placeholder="请粘贴快手分享链接 (https://v.kuaishou.com/...)" style="flex: 1;">
                        <button class="btn btn-primary" onclick="KsParsePage.startDownload()" id="ks-parse-btn">开始下载</button>
                    </div>
                </div>

                <div id="ks-profile-options" style="display: none; margin-top: var(--spacing-md); border-top: 1px solid var(--border-color); padding-top: var(--spacing-md);">
                    <div class="form-group">
                        <label class="form-label">最大抓取页数（每页约12条，填0不限制）</label>
                        <input type="number" id="ks-max-pages" class="form-input" value="5" min="0" style="width: 200px;">
                    </div>
                </div>
            </div>

            <div class="card" id="ks-download-status" style="display: none;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-md);">
                    <h3 style="margin: 0; font-size: 1.1rem;">下载进度</h3>
                </div>
                <div style="display: flex; align-items: center; gap: var(--spacing-md); margin-bottom: var(--spacing-md); flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 200px; height: 8px; background: var(--bg-input); border-radius: 4px; overflow: hidden;">
                        <div id="ks-progress-bar" style="width: 0%; height: 100%; background: var(--gradient-primary); transition: width 0.3s ease;"></div>
                    </div>
                    <span id="ks-progress-text" style="font-variant-numeric: tabular-nums; font-weight: 600; min-width: 45px;">0%</span>
                    <button class="btn btn-secondary btn-sm" onclick="KsParsePage.cancelDownload()" id="ks-cancel-btn" style="padding: 4px 12px; font-size: 0.85rem; height: 32px; display: none; align-items: center; gap: 4px;">
                        取消下载
                    </button>
                </div>
                <div id="ks-log-container" style="background: var(--bg-body); border-radius: var(--radius-sm); padding: var(--spacing-sm); height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; color: var(--text-muted);">
                </div>
            </div>
        `;
    },
    async init() {
        try {
            const data = await API.kuaishou.progress();

            if (data && data.status === 'running') {
                document.getElementById('ks-download-status').style.display = 'block';
                const logContainer = document.getElementById('ks-log-container');
                logContainer.innerHTML = data.logs.map(l => `<div style="margin-bottom: 4px;">${l}</div>`).join('');
                logContainer.scrollTop = logContainer.scrollHeight;

                let pct = 0;
                let processed = (data.downloaded_count || 0) + (data.failed_count || 0);
                if (data.total > 0) {
                    pct = Math.floor((processed / data.total) * 100);
                } else if (data.status === 'completed') {
                    pct = 100;
                }
                document.getElementById('ks-progress-bar').style.width = pct + '%';

                const progressText = document.getElementById('ks-progress-text');
                if (data.total > 1) {
                    progressText.textContent = `${data.downloaded_count || 0}/${data.total}`;
                } else {
                    progressText.textContent = pct + '%';
                }

                const cancelBtn = document.getElementById('ks-cancel-btn');
                if (data.status === 'running') {
                    cancelBtn.style.display = 'flex';
                    this.startProgressPolling();
                } else {
                    cancelBtn.style.display = 'none';
                }
            } else {
                document.getElementById('ks-download-status').style.display = 'none';
            }
        } catch (e) {
            console.error('检查下载进度失败:', e);
        }
    },
    onShow() {
        this.init();
    },
    toggleType() {
        const isProfile = document.querySelector('input[name="ks-parse-type"]:checked').value === 'profile';
        document.getElementById('ks-profile-options').style.display = isProfile ? 'block' : 'none';
    },
    async startDownload() {
        const url = document.getElementById('ks-url-input').value.trim();
        const isProfile = document.querySelector('input[name="ks-parse-type"]:checked').value === 'profile';

        if (!url) {
            Toast.show('请填写链接', 'warning');
            return;
        }

        const btn = document.getElementById('ks-parse-btn');
        btn.disabled = true;
        btn.textContent = '请求中...';

        try {
            if (isProfile) {
                const maxPages = parseInt(document.getElementById('ks-max-pages').value) || 0;
                const data = await API.kuaishou.downloadProfile(url, maxPages);
                if (data.error) throw new Error(data.error);
                Toast.show('批量下载已启动', 'success');
                this.startProgressPolling();
            } else {
                const data = await API.kuaishou.downloadSingle(url);
                if (data.error) throw new Error(data.error);
                Toast.show(`下载完成: ${data.title}`, 'success');
            }
        } catch (err) {
            Toast.show(err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '开始下载';
        }
    },
    startProgressPolling() {
        document.getElementById('ks-download-status').style.display = 'block';
        const logContainer = document.getElementById('ks-log-container');
        const cancelBtn = document.getElementById('ks-cancel-btn');

        if (this.pollTimer) clearInterval(this.pollTimer);

        this.pollTimer = setInterval(async () => {
            try {
                const data = await API.kuaishou.progress();

                let pct = 0;
                let processed = (data.downloaded_count || 0) + (data.failed_count || 0);
                if (data.total > 0) {
                    pct = Math.floor((processed / data.total) * 100);
                } else if (data.status === 'completed') {
                    pct = 100;
                }

                document.getElementById('ks-progress-bar').style.width = pct + '%';

                const progressText = document.getElementById('ks-progress-text');
                if (data.total > 1) {
                    progressText.textContent = `${data.downloaded_count || 0}/${data.total}`;
                } else {
                    progressText.textContent = pct + '%';
                }

                if (data.logs && data.logs.length > 0) {
                    logContainer.innerHTML = data.logs.map(l => `<div style="margin-bottom: 4px;">${l}</div>`).join('');
                    logContainer.scrollTop = logContainer.scrollHeight;
                }

                if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled' || data.status === 'idle') {
                    clearInterval(this.pollTimer);
                    this.pollTimer = null;
                    cancelBtn.style.display = 'none';

                    if (data.status === 'completed') {
                        Toast.show('批量下载完成！', 'success');
                    } else if (data.status === 'cancelled') {
                        Toast.show('下载已取消', 'info');
                    } else if (data.status === 'failed') {
                        Toast.show('下载失败', 'error');
                    }
                } else {
                    cancelBtn.style.display = 'flex';
                }
            } catch (e) {}
        }, 1000);
    },
    async cancelDownload() {
        const cancelBtn = document.getElementById('ks-cancel-btn');
        if (cancelBtn) cancelBtn.style.display = 'none';

        try {
            const res = await API.kuaishou.cancelDownload();
            Toast.show(res.message, 'info');
        } catch (err) {
            Toast.show(err.message, 'error');
        }
    },
    destroy() {
        if (this.pollTimer) clearInterval(this.pollTimer);
    }
};
