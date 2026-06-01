const DyParsePage = {
    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">解析与下载</h2>
                <p class="page-description">粘贴抖音视频/图文链接或主页链接进行下载</p>
            </div>
            
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="form-group">
                    <label class="form-label">链接类型</label>
                    <div style="display: flex; gap: var(--spacing-md); margin-bottom: var(--spacing-md);">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="radio" name="dy-parse-type" value="single" checked onchange="DyParsePage.toggleType()"> 单个视频/图文
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer;">
                            <input type="radio" name="dy-parse-type" value="profile" onchange="DyParsePage.toggleType()"> 用户主页批量下载
                        </label>
                    </div>
                </div>

                <div class="form-group">
                    <label class="form-label">抖音链接</label>
                    <div style="display: flex; gap: var(--spacing-md);">
                        <input type="text" id="dy-url-input" class="form-input" placeholder="请粘贴抖音分享链接 (https://v.douyin.com/...)" style="flex: 1;">
                        <button class="btn btn-primary" onclick="DyParsePage.startDownload()" id="dy-parse-btn">开始下载</button>
                    </div>
                </div>
                
                <div id="dy-profile-options" style="display: none; margin-top: var(--spacing-md); border-top: 1px solid var(--border-color); padding-top: var(--spacing-md);">
                    <div class="form-group">
                        <label class="form-label">最大抓取页数（每页18条，填0不限制）</label>
                        <input type="number" id="dy-max-pages" class="form-input" value="5" min="0" style="width: 200px;">
                    </div>
                </div>
            </div>

            <div class="card" id="dy-download-status" style="display: none;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-md);">
                    <h3 style="margin: 0; font-size: 1.1rem;">下载进度</h3>
                    <button class="btn btn-secondary" onclick="DyParsePage.cancelDownload()" id="dy-cancel-btn">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                            <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                        取消下载
                    </button>
                </div>
                <div style="display: flex; align-items: center; gap: var(--spacing-md); margin-bottom: var(--spacing-md);">
                    <div style="flex: 1; height: 8px; background: var(--bg-input); border-radius: 4px; overflow: hidden;">
                        <div id="dy-progress-bar" style="width: 0%; height: 100%; background: var(--gradient-primary); transition: width 0.3s ease;"></div>
                    </div>
                    <span id="dy-progress-text" style="font-variant-numeric: tabular-nums; font-weight: 600;">0%</span>
                </div>
                <div id="dy-log-container" style="background: var(--bg-body); border-radius: var(--radius-sm); padding: var(--spacing-sm); height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; color: var(--text-muted);">
                </div>
            </div>
        `;
    },
    async init() {},
    toggleType() {
        const isProfile = document.querySelector('input[name="dy-parse-type"]:checked').value === 'profile';
        document.getElementById('dy-profile-options').style.display = isProfile ? 'block' : 'none';
    },
    async startDownload() {
        const url = document.getElementById('dy-url-input').value.trim();
        const isProfile = document.querySelector('input[name="dy-parse-type"]:checked').value === 'profile';
        
        if (!url) {
            Toast.show('请填写链接', 'warning');
            return;
        }

        const btn = document.getElementById('dy-parse-btn');
        btn.disabled = true;
        btn.textContent = '请求中...';

        try {
            if (isProfile) {
                const maxPages = parseInt(document.getElementById('dy-max-pages').value) || 0;
                const res = await fetch('/api/douyin/download-profile', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ url, max_pages: maxPages })
                });
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                Toast.show('批量下载已启动', 'success');
                this.startProgressPolling();
            } else {
                const res = await fetch('/api/douyin/download-single', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ url })
                });
                const data = await res.json();
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
        document.getElementById('dy-download-status').style.display = 'block';
        const logContainer = document.getElementById('dy-log-container');
        const cancelBtn = document.getElementById('dy-cancel-btn');

        if (this.pollTimer) clearInterval(this.pollTimer);

        this.pollTimer = setInterval(async () => {
            try {
                const res = await fetch('/api/douyin/progress');
                const data = await res.json();

                let pct = 0;
                if (data.total > 0) {
                    pct = Math.floor((data.current_index / data.total) * 100);
                } else if (data.status === 'completed') {
                    pct = 100;
                }

                document.getElementById('dy-progress-bar').style.width = pct + '%';
                document.getElementById('dy-progress-text').textContent = pct + '%';

                // update logs
                if (data.logs && data.logs.length > 0) {
                    logContainer.innerHTML = data.logs.map(l => `<div style="margin-bottom: 4px;">${l}</div>`).join('');
                    logContainer.scrollTop = logContainer.scrollHeight;
                }

                // 检查状态
                if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled' || data.status === 'idle') {
                    clearInterval(this.pollTimer);
                    this.pollTimer = null;
                    cancelBtn.disabled = true;

                    if (data.status === 'completed') {
                        Toast.show('批量下载完成！', 'success');
                    } else if (data.status === 'cancelled') {
                        Toast.show('下载已取消', 'info');
                    } else if (data.status === 'failed') {
                        Toast.show('下载失败', 'error');
                    }
                } else {
                    cancelBtn.disabled = false;
                }
            } catch(e) {}
        }, 1000);
    },
    async cancelDownload() {
        const cancelBtn = document.getElementById('dy-cancel-btn');
        cancelBtn.disabled = true;

        try {
            const res = await API.douyin.cancelDownload();
            Toast.show(res.message, 'info');
        } catch (err) {
            Toast.show(err.message, 'error');
            cancelBtn.disabled = false;
        }
    },
    destroy() {
        if (this.pollTimer) clearInterval(this.pollTimer);
    }
};