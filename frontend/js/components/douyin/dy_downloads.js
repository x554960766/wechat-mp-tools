const DyDownloadsPage = {
    history: [],
    loading: false,

    render() {
        return `
            <div class="page-header">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h2 class="page-title">下载历史</h2>
                        <p class="page-description">查看已下载的抖音视频和图集历史记录</p>
                    </div>
                    <div class="btn-group" style="display: flex; gap: 8px;">
                        <button class="btn btn-secondary" onclick="DyDownloadsPage.openFolder()">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px;">
                                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            打开下载文件夹
                        </button>
                        <button class="btn btn-secondary" onclick="DyDownloadsPage.refresh()">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px;">
                                <polyline points="23 4 23 10 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            刷新
                        </button>
                        <button class="btn btn-error" onclick="DyDownloadsPage.clearHistory()">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; color: var(--error);">
                                <polyline points="3 6 5 6 21 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            清空历史
                        </button>
                    </div>
                </div>
            </div>

            <div id="dy-downloads-container">
                <!-- 统计卡片 -->
                <div class="card" style="margin-bottom: var(--spacing-lg);">
                    <div style="display: flex; gap: var(--spacing-2xl); padding: var(--spacing-md) 0;">
                        <div>
                            <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">已下载项目</span>
                            <strong style="font-size: 1.8rem; color: var(--primary);" id="dy-dl-stat-count">0 个</strong>
                        </div>
                        <div style="border-left: 1px solid var(--border-color); padding-left: var(--spacing-2xl);">
                            <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">视频文件</span>
                            <strong style="font-size: 1.8rem; color: var(--text-primary);" id="dy-dl-stat-videos">0 个</strong>
                        </div>
                        <div style="border-left: 1px solid var(--border-color); padding-left: var(--spacing-2xl);">
                            <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">图集文件夹</span>
                            <strong style="font-size: 1.8rem; color: var(--text-primary);" id="dy-dl-stat-images">0 个</strong>
                        </div>
                    </div>
                </div>

                <!-- 历史记录表格 -->
                <div class="card">
                    <div id="dy-downloads-loading" style="text-align: center; padding: var(--spacing-2xl);">
                        <div class="spinner"></div>
                        <p style="margin-top: var(--spacing-md); color: var(--text-muted);">加载中...</p>
                    </div>

                    <div id="dy-downloads-empty" style="display: none; text-align: center; padding: var(--spacing-2xl);">
                        <div style="width: 64px; height: 64px; margin: 0 auto var(--spacing-md); background: rgba(254, 44, 85, 0.1); border-radius: 20px; display: flex; align-items: center; justify-content: center;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 32px; height: 32px; color: var(--primary);">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                        </div>
                        <p style="font-size: 1.1rem; margin-bottom: 8px;">暂无下载历史记录</p>
                        <p style="color: var(--text-muted);">您可以在推荐视频、解析链接或用户主页中下载视频</p>
                    </div>

                    <div id="dy-downloads-content" style="display: none; overflow-x: auto;">
                        <table class="table" style="width: 100%; border-collapse: collapse; text-align: left;">
                            <thead>
                                <tr style="border-bottom: 1px solid var(--border-color);">
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600;">标题</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 100px;">类型</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 120px;">大小</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 180px;">下载时间</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 120px; text-align: right;">操作</th>
                                </tr>
                            </thead>
                            <tbody id="dy-downloads-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        await this.loadHistory();
    },

    onShow() {
        // 命中页面缓存时自动刷新下载历史
        this.loadHistory();
    },

    async loadHistory() {
        this.loading = true;
        this.showLoading();

        try {
            const data = await API.douyin.getHistory();
            this.history = data || [];
            this.renderHistory();
        } catch (err) {
            Toast.show('加载历史记录失败: ' + err.message, 'error');
            this.showEmpty();
        } finally {
            this.loading = false;
            this.hideLoading();
        }
    },

    renderHistory() {
        const tbody = document.getElementById('dy-downloads-tbody');
        const empty = document.getElementById('dy-downloads-empty');
        const content = document.getElementById('dy-downloads-content');

        // Update stats
        const total = this.history.length;
        const videos = this.history.filter(item => item.type === '视频').length;
        const images = this.history.filter(item => item.type === '图文').length;

        document.getElementById('dy-dl-stat-count').textContent = total + ' 个';
        document.getElementById('dy-dl-stat-videos').textContent = videos + ' 个';
        document.getElementById('dy-dl-stat-images').textContent = images + ' 个';

        if (total === 0) {
            empty.style.display = 'block';
            content.style.display = 'none';
            return;
        }

        empty.style.display = 'none';
        content.style.display = 'block';

        tbody.innerHTML = this.history.map((item, index) => {
            const typeClass = item.type === '视频' ? 'badge-primary' : 'badge-success';
            const typeStyle = item.type === '视频' 
                ? 'background: rgba(254, 44, 85, 0.1); color: var(--primary); padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;'
                : 'background: rgba(76, 175, 80, 0.1); color: #4caf50; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;';

            return `
                <tr style="border-bottom: 1px solid var(--border-color); vertical-align: middle; transition: background 0.2s;" onmouseenter="this.style.background='var(--bg-glass-hover)';" onmouseleave="this.style.background='transparent';">
                    <td style="padding: var(--spacing-md); max-width: 350px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <span style="font-weight: 500; color: var(--text-primary);" title="${item.title}">${item.title}</span>
                    </td>
                    <td style="padding: var(--spacing-md);">
                        <span style="${typeStyle}">${item.type}</span>
                    </td>
                    <td style="padding: var(--spacing-md); color: var(--text-muted); font-size: 0.9rem;">
                        ${item.size || '未知'}
                    </td>
                    <td style="padding: var(--spacing-md); color: var(--text-muted); font-size: 0.9rem;">
                        ${item.time}
                    </td>
                    <td style="padding: var(--spacing-md); text-align: right; white-space: nowrap;">
                        <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.openFile('${index}')" style="padding: 4px 10px; font-size: 0.85rem; margin-right: 4px;">
                            播放/打开
                        </button>
                        <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.openParent('${index}')" style="padding: 4px 10px; font-size: 0.85rem; margin-right: 4px;">
                            📂 打开目录
                        </button>
                        ${item.type === '视频' ? `
                        <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.importToTranscode('${index}')" style="padding: 4px 10px; font-size: 0.85rem; background: var(--gradient-primary); color: white;">
                            导入转码
                        </button>
                        ` : ''}
                    </td>
                </tr>
            `;
        }).join('');
    },

    async openFolder() {
        try {
            await API.douyin.openFolder();
            Toast.show('已打开下载文件夹', 'success');
        } catch (err) {
            Toast.show('打开失败: ' + err.message, 'error');
        }
    },

    async openFile(index) {
        const item = this.history[index];
        if (!item || !item.path) {
            Toast.show('无效的下载记录', 'error');
            return;
        }

        try {
            await API.douyin.openFile(item.path);
            Toast.show('正在打开文件...', 'info');
        } catch (err) {
            Toast.show(err.message, 'error');
        }
    },

    async openParent(index) {
        const item = this.history[index];
        if (!item || !item.path) {
            Toast.show('无效的下载记录', 'error');
            return;
        }

        try {
            await API.douyin.openParent(item.path);
            Toast.show('正在打开目录...', 'info');
        } catch (err) {
            Toast.show(err.message, 'error');
        }
    },

    async clearHistory() {
        Modal.confirm('清空下载历史', '您确定要清空抖音下载历史记录吗？（注意：这不会删除您本地已下载的视频和图片文件）', () => {
            Modal.confirm('确认清空', '此操作将永久删除抖音下载历史记录，且不可恢复！确定要继续吗？', async () => {
                try {
                    await API.douyin.clearHistory();
                    Toast.show('历史记录已清空', 'success');
                    await DyDownloadsPage.refresh();
                } catch (err) {
                    Toast.show('清空失败: ' + err.message, 'error');
                }
            });
        });
    },

    async refresh() {
        await this.loadHistory();
    },

    showLoading() {
        const loading = document.getElementById('dy-downloads-loading');
        if (loading) loading.style.display = 'block';
        const content = document.getElementById('dy-downloads-content');
        if (content) content.style.display = 'none';
        const empty = document.getElementById('dy-downloads-empty');
        if (empty) empty.style.display = 'none';
    },

    hideLoading() {
        const loading = document.getElementById('dy-downloads-loading');
        if (loading) loading.style.display = 'none';
    },

    showEmpty() {
        const empty = document.getElementById('dy-downloads-empty');
        if (empty) empty.style.display = 'block';
        const content = document.getElementById('dy-downloads-content');
        if (content) content.style.display = 'none';
    },

    async importToTranscode(index) {
        const item = this.history[index];
        if (!item || !item.path) {
            Toast.show('无效的视频路径', 'error');
            return;
        }
        
        Toast.show('正在解析视频路径...', 'info');
        try {
            const res = await API.post('/api/transcode/resolve-path', { path: item.path });
            if (res && res.success && res.path) {
                Toast.show('解析成功，正在跳转到转码页面...', 'success');
                setTimeout(() => {
                    Router.navigate(`transcode?path=${encodeURIComponent(res.path)}&name=${encodeURIComponent(res.name)}`);
                }, 500);
            } else {
                Toast.show(res.error || '该下载未包含支持的视频文件', 'error');
            }
        } catch (err) {
            Toast.show(err.message || '解析视频路径失败，可能不包含视频文件', 'error');
        }
    },

    destroy() {
        this.history = [];
    }
};