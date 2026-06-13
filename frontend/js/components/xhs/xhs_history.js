/**
 * 小红书下载历史组件
 */
const XhsHistoryPage = {
    _history: [],

    render() {
        return `
            <div class="page-header" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h2 class="page-title">小红书下载历史</h2>
                    <p class="page-description">浏览和管理您下载的小红书笔记、视频与图文记录，支持直接打开文件和定位到本地文件夹。</p>
                </div>
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-secondary" onclick="XhsHistoryPage.openDownloadsFolder()">📂 打开下载文件夹</button>
                    <button class="btn btn-danger" onclick="XhsHistoryPage.clearHistory()">🧹 清空历史</button>
                </div>
            </div>

            <!-- Stats Bar -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 16px; margin-bottom: 20px;">
                <div class="card" style="padding: 16px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; text-align: center;">
                    <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 4px;">总计下载</div>
                    <div id="xhs-history-stat-total" style="font-size: 1.8rem; font-weight: 700; color: var(--text-primary);">0</div>
                </div>
                <div class="card" style="padding: 16px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; text-align: center;">
                    <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 4px;">🎬 视频数量</div>
                    <div id="xhs-history-stat-video" style="font-size: 1.8rem; font-weight: 700; color: var(--success);">0</div>
                </div>
                <div class="card" style="padding: 16px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; text-align: center;">
                    <div style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 4px;">📸 图文图集</div>
                    <div id="xhs-history-stat-image" style="font-size: 1.8rem; font-weight: 700; color: var(--primary);">0</div>
                </div>
            </div>

            <!-- Table Card -->
            <div class="card" style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; overflow: hidden; margin-bottom: 30px;">
                <div style="overflow-x: auto;">
                    <table class="table" style="width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem;">
                        <thead>
                            <tr style="border-bottom: 1px solid var(--border-color); background: var(--bg-tertiary); color: var(--text-secondary); font-weight: 600;">
                                <th style="padding: 12px 16px;">标题</th>
                                <th style="padding: 12px 16px; width: 80px;">类型</th>
                                <th style="padding: 12px 16px; width: 120px;">作者</th>
                                <th style="padding: 12px 16px; width: 100px;">大小</th>
                                <th style="padding: 12px 16px; width: 160px;">时间</th>
                                <th style="padding: 12px 16px; width: 160px; text-align: center;">操作</th>
                            </tr>
                        </thead>
                        <tbody id="xhs-history-table-body">
                            <tr>
                                <td colspan="6" style="text-align: center; padding: 40px; color: var(--text-muted);">
                                    <div class="spinner" style="margin: 0 auto 12px;"></div>
                                    正在加载历史记录...
                                </td>
                            </tr>
                        </tbody>
                    </table>
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
        try {
            const data = await API.xhs.getHistory();
            this._history = data.history || [];
            this.renderStats();
            this.renderTable();
        } catch (err) {
            const tbody = document.getElementById('xhs-history-table-body');
            if (tbody) {
                tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 40px; color: var(--error);">加载历史失败: ${err.message}</td></tr>`;
            }
        }
    },

    renderStats() {
        const totalEl = document.getElementById('xhs-history-stat-total');
        const videoEl = document.getElementById('xhs-history-stat-video');
        const imageEl = document.getElementById('xhs-history-stat-image');

        if (!totalEl || !videoEl || !imageEl) return;

        const total = this._history.length;
        const videos = this._history.filter(item => item.type === '视频').length;
        const images = total - videos;

        totalEl.textContent = total;
        videoEl.textContent = videos;
        imageEl.textContent = images;
    },

    renderTable() {
        const tbody = document.getElementById('xhs-history-table-body');
        if (!tbody) return;

        if (this._history.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; padding: 40px; color: var(--text-muted);">暂无下载历史记录</td></tr>`;
            return;
        }

        tbody.innerHTML = this._history.map(item => {
            const title = item.title ? this._esc(item.title) : '无标题笔记';
            const author = item.author ? this._esc(item.author) : '未知';
            const timeStr = item.time ? new Date(item.time * 1000).toLocaleString('zh-CN') : '未知';
            const isVideo = item.type === '视频';
            const badgeBg = isVideo ? 'rgba(7,193,96,0.1)' : 'rgba(0,122,255,0.1)';
            const badgeColor = isVideo ? '#07c160' : 'var(--primary)';
            const sizeStr = item.size ? this.formatBytes(item.size) : '未知';
            
            let titleStyle = 'font-weight: 500; color: var(--text-primary);';
            let statusPrefix = '';
            if (!item.success) {
                titleStyle = 'color: var(--error); text-decoration: line-through;';
                statusPrefix = `<span style="color:var(--error); font-size:0.75rem; margin-right:4px;">[失败]</span>`;
            }

            return `
                <tr style="border-bottom: 1px solid var(--border-color); vertical-align: middle;">
                    <td style="padding: 12px 16px; max-width: 300px;">
                        <div style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; ${titleStyle}" title="${title}">
                            ${statusPrefix}${title}
                        </div>
                        ${item.error ? `<div style="font-size: 0.75rem; color: var(--error); margin-top: 2px;">错误: ${this._esc(item.error)}</div>` : ''}
                    </td>
                    <td style="padding: 12px 16px;">
                        <span style="background: ${badgeBg}; color: ${badgeColor}; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600;">
                            ${isVideo ? '🎬 视频' : '📸 图文'}
                        </span>
                    </td>
                    <td style="padding: 12px 16px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 120px;" title="${author}">
                        ${author}
                    </td>
                    <td style="padding: 12px 16px;">${sizeStr}</td>
                    <td style="padding: 12px 16px; color: var(--text-muted); font-size: 0.8rem;">${timeStr}</td>
                    <td style="padding: 12px 16px; text-align: center;">
                        <div style="display: flex; gap: 6px; justify-content: center;">
                            ${item.success && item.path ? `
                                <button class="btn btn-secondary btn-sm" onclick="XhsHistoryPage.openFile('${this._esc(item.path).replace(/\\/g, '\\\\')}')" style="padding: 3px 8px; font-size: 0.75rem;">📂 打开</button>
                                <button class="btn btn-secondary btn-sm" onclick="XhsHistoryPage.openParent('${this._esc(item.path).replace(/\\/g, '\\\\')}')" style="padding: 3px 8px; font-size: 0.75rem;">🔍 定位</button>
                            ` : ''}
                            <button class="btn btn-danger btn-sm" onclick="XhsHistoryPage.deleteItem(${item._index}, '${title}')" style="padding: 3px 8px; font-size: 0.75rem;">删除</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    },

    formatBytes(bytes, decimals = 2) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    },

    async openDownloadsFolder() {
        try {
            await API.xhs.openFolder();
        } catch (err) {
            Toast.error('打开文件夹失败: ' + err.message);
        }
    },

    async openFile(path) {
        try {
            await API.xhs.openFile(path);
        } catch (err) {
            Toast.error('打开文件失败: ' + err.message);
        }
    },

    async openParent(path) {
        try {
            await API.xhs.openParent(path);
        } catch (err) {
            Toast.error('定位父目录失败: ' + err.message);
        }
    },

    deleteItem(index, title) {
        Modal.confirm('删除记录', `确定要删除「${title}」的下载记录及本地文件吗？<br><strong style="color:var(--error);">此操作将连带删除对应的本地下载文件/文件夹，且不可恢复！</strong>`, async () => {
            try {
                const res = await API.xhs.deleteHistory(index);
                Toast.success(res.message);
                await this.loadHistory();
            } catch (err) {
                Toast.error('删除失败: ' + err.message);
            }
        });
    },

    clearHistory() {
        Modal.confirm('清空下载历史', '确定要清空所有下载历史记录吗？<br><strong>说明：此操作仅会清除软件中的历史显示记录，不会删除本地已下载的任何媒体文件！</strong>', async () => {
            try {
                await API.xhs.clearHistory();
                Toast.success('历史记录已清空');
                await this.loadHistory();
            } catch (err) {
                Toast.error('清空失败: ' + err.message);
            }
        });
    },

    _esc(s) {
        if (!s) return '';
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }
};
