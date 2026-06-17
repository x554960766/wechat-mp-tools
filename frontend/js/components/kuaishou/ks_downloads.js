const KsDownloadsPage = {
    history: [],
    loading: false,

    render() {
        return `
            <div class="page-header">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <h2 class="page-title">下载历史</h2>
                        <p class="page-description">查看已下载的快手视频和图集历史记录</p>
                    </div>
                    <div class="btn-group" style="display: flex; gap: 8px;">
                        <button class="btn btn-secondary" onclick="KsDownloadsPage.openFolder()">打开下载文件夹</button>
                        <button class="btn btn-secondary" onclick="KsDownloadsPage.refresh()">刷新</button>
                        <button class="btn btn-error" onclick="KsDownloadsPage.clearHistory()">清空历史</button>
                    </div>
                </div>
            </div>

            <div id="ks-downloads-container">
                <div class="card" style="margin-bottom: var(--spacing-lg);">
                    <div style="display: flex; gap: var(--spacing-2xl); padding: var(--spacing-md) 0;">
                        <div>
                            <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">已下载项目</span>
                            <strong style="font-size: 1.8rem; color: var(--primary);" id="ks-dl-stat-count">0 个</strong>
                        </div>
                        <div style="border-left: 1px solid var(--border-color); padding-left: var(--spacing-2xl);">
                            <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">视频文件</span>
                            <strong style="font-size: 1.8rem; color: var(--text-primary);" id="ks-dl-stat-videos">0 个</strong>
                        </div>
                        <div style="border-left: 1px solid var(--border-color); padding-left: var(--spacing-2xl);">
                            <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">图集文件夹</span>
                            <strong style="font-size: 1.8rem; color: var(--text-primary);" id="ks-dl-stat-images">0 个</strong>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <div id="ks-downloads-loading" style="text-align: center; padding: var(--spacing-2xl);">
                        <div class="spinner"></div>
                        <p style="margin-top: var(--spacing-md); color: var(--text-muted);">加载中...</p>
                    </div>

                    <div id="ks-downloads-empty" style="display: none; text-align: center; padding: var(--spacing-2xl);">
                        <p style="font-size: 1.1rem; margin-bottom: 8px;">暂无下载历史记录</p>
                        <p style="color: var(--text-muted);">您可以在「解析与下载」中下载快手视频</p>
                    </div>

                    <div id="ks-downloads-content" style="display: none; overflow-x: auto;">
                        <table class="table" style="width: 100%; border-collapse: collapse; text-align: left;">
                            <thead>
                                <tr style="border-bottom: 1px solid var(--border-color);">
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600;">标题</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 100px;">类型</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 120px;">大小</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 180px;">下载时间</th>
                                    <th style="padding: 12px var(--spacing-md); color: var(--text-muted); font-weight: 600; width: 160px; text-align: right;">操作</th>
                                </tr>
                            </thead>
                            <tbody id="ks-downloads-tbody"></tbody>
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
        this.loadHistory();
    },

    async loadHistory() {
        this.loading = true;
        this.showLoading();
        try {
            const data = await API.kuaishou.getHistory();
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
        const tbody = document.getElementById('ks-downloads-tbody');
        const empty = document.getElementById('ks-downloads-empty');
        const content = document.getElementById('ks-downloads-content');

        const total = this.history.length;
        const videos = this.history.filter(item => item.type === '视频').length;
        const images = this.history.filter(item => item.type === '图文').length;

        document.getElementById('ks-dl-stat-count').textContent = total + ' 个';
        document.getElementById('ks-dl-stat-videos').textContent = videos + ' 个';
        document.getElementById('ks-dl-stat-images').textContent = images + ' 个';

        if (total === 0) {
            empty.style.display = 'block';
            content.style.display = 'none';
            return;
        }

        empty.style.display = 'none';
        content.style.display = 'block';

        tbody.innerHTML = this.history.map((item, index) => {
            const typeStyle = item.type === '视频'
                ? 'background: rgba(254, 44, 85, 0.1); color: var(--primary); padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;'
                : 'background: rgba(76, 175, 80, 0.1); color: #4caf50; padding: 4px 8px; border-radius: 4px; font-size: 0.8rem; font-weight: 500;';

            return `
                <tr style="border-bottom: 1px solid var(--border-color); vertical-align: middle; transition: background 0.2s;" onmouseenter="this.style.background='var(--bg-glass-hover)';" onmouseleave="this.style.background='transparent';">
                    <td style="padding: var(--spacing-md); max-width: 350px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                        <span style="font-weight: 500; color: var(--text-primary);" title="${item.title}">${item.title}</span>
                    </td>
                    <td style="padding: var(--spacing-md);"><span style="${typeStyle}">${item.type}</span></td>
                    <td style="padding: var(--spacing-md); color: var(--text-muted); font-size: 0.9rem;">${item.size || '未知'}</td>
                    <td style="padding: var(--spacing-md); color: var(--text-muted); font-size: 0.9rem;">${item.time}</td>
                    <td style="padding: var(--spacing-md); text-align: right; white-space: nowrap;">
                        <button class="btn btn-secondary btn-sm" onclick="KsDownloadsPage.openFile('${index}')" style="padding: 4px 10px; font-size: 0.85rem; margin-right: 4px;">播放/打开</button>
                        <button class="btn btn-secondary btn-sm" onclick="KsDownloadsPage.openParent('${index}')" style="padding: 4px 10px; font-size: 0.85rem;">📂 打开目录</button>
                    </td>
                </tr>
            `;
        }).join('');
    },

    async openFolder() {
        try {
            await API.kuaishou.openFolder();
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
            await API.kuaishou.openFile(item.path);
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
            await API.kuaishou.openParent(item.path);
            Toast.show('正在打开目录...', 'info');
        } catch (err) {
            Toast.show(err.message, 'error');
        }
    },

    async clearHistory() {
        Modal.confirm('清空下载历史', '您确定要清空快手下载历史记录吗？（注意：这不会删除您本地已下载的视频和图片文件）', async () => {
            try {
                await API.kuaishou.clearHistory();
                Toast.show('历史记录已清空', 'success');
                await KsDownloadsPage.refresh();
            } catch (err) {
                Toast.show('清空失败: ' + err.message, 'error');
            }
        });
    },

    async refresh() {
        await this.loadHistory();
    },

    showLoading() {
        const loading = document.getElementById('ks-downloads-loading');
        if (loading) loading.style.display = 'block';
        const content = document.getElementById('ks-downloads-content');
        if (content) content.style.display = 'none';
        const empty = document.getElementById('ks-downloads-empty');
        if (empty) empty.style.display = 'none';
    },

    hideLoading() {
        const loading = document.getElementById('ks-downloads-loading');
        if (loading) loading.style.display = 'none';
    },

    showEmpty() {
        const empty = document.getElementById('ks-downloads-empty');
        if (empty) empty.style.display = 'block';
        const content = document.getElementById('ks-downloads-content');
        if (content) content.style.display = 'none';
    },

    destroy() {
        this.history = [];
    }
};
