const DyParsePage = {
    detectedData: null,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">解析与下载</h2>
                <p class="page-description">粘贴抖音视频/图文、合集、音乐或主页链接进行下载</p>
            </div>
            
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="form-group" style="margin-bottom: var(--spacing-md);">
                    <label class="form-label">抖音链接</label>
                    <div style="display: flex; gap: var(--spacing-md);">
                        <input type="text" id="dy-url-input" class="form-input" placeholder="请粘贴抖音分享链接 (https://v.douyin.com/...)" style="flex: 1;" oninput="DyParsePage.onUrlInput()">
                        <button class="btn btn-secondary" onclick="DyParsePage.detectUrl()" id="dy-detect-btn">检测链接</button>
                    </div>
                </div>

                <div id="dy-detection-status" style="display: none; margin-bottom: var(--spacing-md); padding: var(--spacing-sm); background: rgba(102, 126, 234, 0.08); border-radius: var(--radius-sm); border: 1px solid rgba(102, 126, 234, 0.2); font-size: 0.88rem; color: var(--text-primary); align-items: center; gap: 8px;">
                    <span style="width: 8px; height: 8px; background: #10b981; border-radius: 50%;"></span>
                    <span id="dy-detection-text"></span>
                </div>

                <div id="dy-config-container" style="display: none; border-top: 1px solid var(--border-color); padding-top: var(--spacing-md); margin-top: var(--spacing-md);">
                    <h3 style="font-size: 1.05rem; margin-top: 0; margin-bottom: var(--spacing-xs); font-weight: 600;">请选择下载内容 (至少一项)</h3>
                    <p style="font-size: 0.82rem; color: var(--text-muted); margin-bottom: var(--spacing-md);">检测到该链接为博主主页，支持组合下载多个分类。</p>
                    
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: var(--spacing-md); margin-bottom: var(--spacing-md);">
                        <div class="option-card active" id="card-post" onclick="DyParsePage.toggleCard('post')">
                            <div class="option-card-header">
                                <input type="checkbox" id="chk-post" checked onclick="event.stopPropagation(); DyParsePage.updateCardActive('post')">
                                <span class="option-card-title">作品</span>
                            </div>
                            <div class="option-card-desc">博主发布的视频与图集</div>
                        </div>
                        <div class="option-card" id="card-like" onclick="DyParsePage.toggleCard('like')">
                            <div class="option-card-header">
                                <input type="checkbox" id="chk-like" onclick="event.stopPropagation(); DyParsePage.updateCardActive('like')">
                                <span class="option-card-title">喜欢</span>
                            </div>
                            <div class="option-card-desc">博主公开的点赞视频列表</div>
                        </div>
                        <div class="option-card" id="card-mix" onclick="DyParsePage.toggleCard('mix')">
                            <div class="option-card-header">
                                <input type="checkbox" id="chk-mix" onclick="event.stopPropagation(); DyParsePage.updateCardActive('mix')">
                                <span class="option-card-title">合集</span>
                            </div>
                            <div class="option-card-desc">博主创建的视频合集列表</div>
                        </div>
                        <div class="option-card" id="card-story" onclick="DyParsePage.toggleCard('story')">
                            <div class="option-card-header">
                                <input type="checkbox" id="chk-story" onclick="event.stopPropagation(); DyParsePage.updateCardActive('story')">
                                <span class="option-card-title">日常</span>
                            </div>
                            <div class="option-card-desc">博主发布的日常视频</div>
                        </div>
                    </div>
                    
                    <div class="form-group" style="margin-bottom: 0;">
                        <label class="form-label">最大抓取页数 (每页18条，填 0 不限制页数)</label>
                        <input type="number" id="dy-max-pages" class="form-input" value="5" min="0" style="width: 200px;">
                    </div>
                </div>

                <div style="display: flex; justify-content: flex-end; margin-top: var(--spacing-md);" id="dy-download-btn-wrapper">
                    <button class="btn btn-primary" onclick="DyParsePage.startDownload()" id="dy-parse-btn">开始下载</button>
                </div>
            </div>

            <div class="card" id="dy-download-status" style="display: none;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-md);">
                    <h3 style="margin: 0; font-size: 1.1rem;">下载进度</h3>
                </div>
                <div style="display: flex; align-items: center; gap: var(--spacing-md); margin-bottom: var(--spacing-md); flex-wrap: wrap;">
                    <div style="flex: 1; min-width: 200px; height: 8px; background: var(--bg-input); border-radius: 4px; overflow: hidden;">
                        <div id="dy-progress-bar" style="width: 0%; height: 100%; background: var(--gradient-primary); transition: width 0.3s ease;"></div>
                    </div>
                    <span id="dy-progress-text" style="font-variant-numeric: tabular-nums; font-weight: 600; min-width: 45px;">0%</span>
                    <button class="btn btn-secondary btn-sm" onclick="DyParsePage.cancelDownload()" id="dy-cancel-btn" style="padding: 4px 12px; font-size: 0.85rem; height: 32px; display: none; align-items: center; gap: 4px;">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 14px; height: 14px; display: inline-block; vertical-align: text-bottom;">
                            <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                        取消下载
                    </button>
                </div>
                <div id="dy-log-container" style="background: var(--bg-body); border-radius: var(--radius-sm); padding: var(--spacing-sm); height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.85rem; color: var(--text-muted);">
                </div>
            </div>
        `;
    },

    async init() {
        this.detectedData = null;
        try {
            const res = await fetch('/api/douyin/progress');
            const data = await res.json();
            
            if (data && (data.status === 'running' || (data.logs && data.logs.length > 0))) {
                document.getElementById('dy-download-status').style.display = 'block';
                const logContainer = document.getElementById('dy-log-container');
                logContainer.innerHTML = data.logs.map(l => `<div style="margin-bottom: 4px;">${l}</div>`).join('');
                logContainer.scrollTop = logContainer.scrollHeight;

                let pct = 0;
                let processed = (data.downloaded_count || 0) + (data.failed_count || 0);
                if (data.total > 0) {
                    pct = Math.floor((processed / data.total) * 100);
                } else if (data.status === 'completed') {
                    pct = 100;
                }
                document.getElementById('dy-progress-bar').style.width = pct + '%';
                
                const progressText = document.getElementById('dy-progress-text');
                if (data.total > 1) {
                    progressText.textContent = `${data.downloaded_count || 0}/${data.total}`;
                } else {
                    progressText.textContent = pct + '%';
                }

                const cancelBtn = document.getElementById('dy-cancel-btn');
                if (data.status === 'running') {
                    cancelBtn.style.display = 'flex';
                    this.startProgressPolling();
                } else {
                    cancelBtn.style.display = 'none';
                }
            } else {
                document.getElementById('dy-download-status').style.display = 'none';
            }
        } catch (e) {
            console.error('检查下载进度失败:', e);
        }
    },

    onShow() {
        this.init();
    },

    onUrlInput() {
        // 当链接被修改时，重置检测状态
        if (this.detectedData) {
            this.detectedData = null;
            document.getElementById('dy-detection-status').style.display = 'none';
            document.getElementById('dy-config-container').style.display = 'none';
        }
    },

    async detectUrl() {
        const url = document.getElementById('dy-url-input').value.trim();
        if (!url) {
            Toast.show('请填写链接', 'warning');
            return;
        }

        const detectBtn = document.getElementById('dy-detect-btn');
        detectBtn.disabled = true;
        detectBtn.textContent = '检测中...';

        try {
            const res = await fetch('/api/douyin/detect-url', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            this.detectedData = data;

            // 显示识别信息
            const statusDiv = document.getElementById('dy-detection-status');
            const statusText = document.getElementById('dy-detection-text');
            statusText.textContent = '已识别：' + data.message;
            statusDiv.style.display = 'flex';

            // 如果是博主主页，展示配置面板
            if (data.type === 'user') {
                document.getElementById('dy-config-container').style.display = 'block';
            } else {
                document.getElementById('dy-config-container').style.display = 'none';
            }
        } catch (err) {
            Toast.show(err.message, 'error');
            this.detectedData = null;
        } finally {
            detectBtn.disabled = false;
            detectBtn.textContent = '检测链接';
        }
    },

    toggleCard(type) {
        const chk = document.getElementById(`chk-${type}`);
        chk.checked = !chk.checked;
        this.updateCardActive(type);
    },

    updateCardActive(type) {
        const card = document.getElementById(`card-${type}`);
        const chk = document.getElementById(`chk-${type}`);
        if (chk.checked) {
            card.classList.add('active');
        } else {
            card.classList.remove('active');
        }
    },

    async startDownload() {
        const url = document.getElementById('dy-url-input').value.trim();
        if (!url) {
            Toast.show('请填写链接', 'warning');
            return;
        }

        const btn = document.getElementById('dy-parse-btn');
        btn.disabled = true;
        btn.textContent = '下载启动中...';

        // 1. 如果没有进行链接检测，则先自动调用检测
        if (!this.detectedData) {
            await this.detectUrl();
            if (!this.detectedData) {
                btn.disabled = false;
                btn.textContent = '开始下载';
                return;
            }
            // 如果自动检测出是博主主页，则展开配置并停下，让用户确认/选择下载项
            if (this.detectedData.type === 'user') {
                btn.disabled = false;
                btn.textContent = '开始下载';
                Toast.show('已识别博主主页，请在下方选择下载内容后再次点击下载', 'info');
                return;
            }
        }

        const data = this.detectedData;

        // 2. 准备立即显示本地进度卡片
        const statusCard = document.getElementById('dy-download-status');
        const logContainer = document.getElementById('dy-log-container');
        statusCard.style.display = 'block';
        document.getElementById('dy-progress-bar').style.width = '0%';
        document.getElementById('dy-progress-text').textContent = '0%';
        
        const timestamp = new Date().toLocaleTimeString();
        logContainer.innerHTML = `<div style="margin-bottom: 4px; color: var(--color-primary);">[${timestamp}] 🚀 正在启动下载任务...</div>`;

        try {
            if (data.type === 'user') {
                // 收集勾选的项
                const types = [];
                if (document.getElementById('chk-post').checked) types.push('post');
                if (document.getElementById('chk-like').checked) types.push('like');
                if (document.getElementById('chk-mix').checked) types.push('mix');
                if (document.getElementById('chk-story').checked) types.push('story');

                if (types.length === 0) {
                    throw new Error('请至少选择一项要下载的内容 (作品/喜欢/合集/日常)');
                }

                const maxPages = parseInt(document.getElementById('dy-max-pages').value) || 0;

                const res = await fetch('/api/douyin/download-user', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        sec_uid: data.id,
                        types: types,
                        max_pages: maxPages
                    })
                });
                const resData = await res.json();
                if (resData.error) throw new Error(resData.error);

                Toast.show('批量下载任务已成功启动', 'success');
                this.startProgressPolling();
            } else {
                // 单条、合集或音乐下载
                const res = await fetch('/api/douyin/download-single', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ url })
                });
                const resData = await res.json();
                if (resData.error) throw new Error(resData.error);

                if (resData.task_started) {
                    Toast.show(resData.message || '批量下载已启动', 'success');
                    this.startProgressPolling();
                } else {
                    const finishTime = new Date().toLocaleTimeString();
                    logContainer.innerHTML += `<div style="margin-bottom: 4px; color: #10b981;">[${finishTime}] ✅ 下载完成: ${resData.title}</div>`;
                    document.getElementById('dy-progress-bar').style.width = '100%';
                    document.getElementById('dy-progress-text').textContent = '100%';
                    Toast.show(`下载完成: ${resData.title}`, 'success');
                }
            }
        } catch (err) {
            Toast.show(err.message, 'error');
            const errorTime = new Date().toLocaleTimeString();
            logContainer.innerHTML += `<div style="margin-bottom: 4px; color: #ef4444;">[${errorTime}] ❌ 任务启动失败: ${err.message}</div>`;
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
                let processed = (data.downloaded_count || 0) + (data.failed_count || 0);
                if (data.total > 0) {
                    pct = Math.floor((processed / data.total) * 100);
                } else if (data.status === 'completed') {
                    pct = 100;
                }

                document.getElementById('dy-progress-bar').style.width = pct + '%';
                
                const progressText = document.getElementById('dy-progress-text');
                if (data.total > 1) {
                    progressText.textContent = `${data.downloaded_count || 0}/${data.total}`;
                } else {
                    progressText.textContent = pct + '%';
                }

                // update logs
                if (data.logs && data.logs.length > 0) {
                    logContainer.innerHTML = data.logs.map(l => `<div style="margin-bottom: 4px;">${l}</div>`).join('');
                    logContainer.scrollTop = logContainer.scrollHeight;
                }

                // 检查状态
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
            } catch(e) {}
        }, 1000);
    },

    async cancelDownload() {
        const cancelBtn = document.getElementById('dy-cancel-btn');
        if (cancelBtn) cancelBtn.style.display = 'none';
 
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