/**
 * 网址下载页面组件
 */
const DownloadPage = {
    downloadTaskId: null,
    _pollTimer: null,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">网址下载</h2>
                <p class="page-description">输入微信公众号文章 URL，直接下载文章内容（含图片和视频）</p>
            </div>

            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="card-header">
                    <h3 class="card-title">🔗 输入文章网址</h3>
                </div>

                <div class="form-group">
                    <label class="form-label">文章 URL（每行一个，支持批量下载）</label>
                    <textarea class="form-textarea" id="url-input"
                              placeholder="https://mp.weixin.qq.com/s/xxxxxx&#10;https://mp.weixin.qq.com/s/yyyyyy&#10;&#10;每行输入一个微信文章链接..."
                              rows="6"></textarea>
                    <div class="form-hint">支持 mp.weixin.qq.com 格式的文章链接，每行一个</div>
                </div>

                <div class="btn-group">
                    <button class="btn btn-primary" onclick="DownloadPage.startDownload()" id="btn-url-download">
                        📥 开始下载
                    </button>
                    <button class="btn btn-secondary" onclick="DownloadPage.clearInput()">
                        清空
                    </button>
                    <button class="btn btn-secondary" onclick="DownloadPage.pasteFromClipboard()">
                        📋 粘贴
                    </button>
                </div>
            </div>

            <!-- 下载进度 -->
            <div id="url-download-progress" style="display: none;">
                <div class="download-progress-card">
                    <div class="download-progress-header" style="display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <h3 class="card-title" style="color: #000000;">📥 下载进度</h3>
                            <span class="badge badge-info" id="url-download-badge">下载中</span>
                        </div>
                        <button class="btn btn-primary btn-sm" onclick="DownloadPage.openFolder()" style="padding: 4px 10px; font-size: 0.85rem;">
                            📂 打开下载目录
                        </button>
                    </div>
                    <div class="progress-bar" style="margin-top: 12px;">
                        <div class="progress-fill" id="url-progress-bar" style="width: 0%"></div>
                    </div>
                    <div class="download-progress-stats">
                        <span>当前: <strong id="url-download-current">-</strong></span>
                        <span>完成: <strong id="url-download-completed">0</strong></span>
                        <span>失败: <strong id="url-download-failed">0</strong></span>
                        <span>总计: <strong id="url-download-total">0</strong></span>
                    </div>
                    <div id="url-download-note" style="margin-top: 12px; color: var(--text-muted); font-size: 0.85rem;">
                        下载完成后可在“下载历史”中查看文件列表。
                    </div>
                </div>
            </div>
        `;
    },

    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async startDownload() {
        const textarea = document.getElementById('url-input');
        const text = textarea?.value.trim();

        if (!text) {
            Toast.warning('请输入文章 URL');
            return;
        }

        const urls = text.split('\n')
            .map(u => u.trim())
            .filter(u => u && u.startsWith('http'));

        if (urls.length === 0) {
            Toast.warning('未检测到有效的 URL');
            return;
        }

        const btn = document.getElementById('btn-url-download');
        btn.disabled = true;
        btn.textContent = '启动中...';

        try {
            const data = await API.articles.downloadByUrl(urls);
            this.downloadTaskId = data.task_id;
            Toast.success(data.message);

            document.getElementById('url-download-progress').style.display = 'block';
            this.startProgressPolling();
        } catch (err) {
            // error shown by API
        } finally {
            btn.disabled = false;
            btn.innerHTML = '📥 开始下载';
        }
    },

    startProgressPolling() {
        if (this._pollTimer) clearInterval(this._pollTimer);

        this._pollTimer = setInterval(async () => {
            try {
                const task = await API.articles.downloadStatus(this.downloadTaskId);
                this.updateProgress(task);

                if (task.status === 'completed' || task.status === 'failed') {
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                }
            } catch (err) {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
            }
        }, 1000);
    },

    updateProgress(task) {
        const total = task.total || 1;
        const completed = task.completed || 0;
        const failed = task.failed || 0;
        const progress = Math.round(((completed + failed) / total) * 100);

        document.getElementById('url-progress-bar').style.width = `${progress}%`;
        document.getElementById('url-download-current').textContent = task.current || '-';
        document.getElementById('url-download-completed').textContent = completed;
        document.getElementById('url-download-failed').textContent = failed;
        document.getElementById('url-download-total').textContent = total;

        const badge = document.getElementById('url-download-badge');
        if (task.status === 'completed') {
            badge.className = 'badge badge-success';
            badge.textContent = '已完成';
            Toast.success(`下载完成！成功 ${completed} 篇，失败 ${failed} 篇`);
        } else if (task.status === 'failed') {
            badge.className = 'badge badge-error';
            badge.textContent = '失败';
        }

        const note = document.getElementById('url-download-note');
        if (note && task.status === 'completed') {
            note.innerHTML = '下载完成。请到 <a href="#history" style="color: var(--primary); text-decoration: none;">下载历史</a> 查看文件列表。';
        }
    },

    clearInput() {
        document.getElementById('url-input').value = '';
    },

    async pasteFromClipboard() {
        try {
            const text = await navigator.clipboard.readText();
            document.getElementById('url-input').value = text;
            Toast.success('已粘贴剪贴板内容');
        } catch (err) {
            Toast.warning('无法读取剪贴板，请手动粘贴');
        }
    },

    async openFolder() {
        try {
            await API.articles.openFolder('url_download');
            Toast.success('下载目录已打开');
        } catch (err) {
            // shown by API
        }
    },

};
