/**
 * 下载历史页面组件
 */
const HistoryPage = {
    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">下载历史</h2>
                <p class="page-description">查看已下载的文章记录</p>
            </div>

            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">下载记录</h3>
                    <div class="btn-group">
                        <button class="btn btn-primary btn-sm" onclick="HistoryPage.openFolder()">打开下载目录</button>
                        <button class="btn btn-secondary btn-sm" onclick="HistoryPage.clearHistory()">清空历史</button>
                    </div>
                </div>
                <div id="download-history">
                    <div class="loading-screen" style="min-height: 100px;">
                        <div class="spinner"></div>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        await this.loadHistory();
    },

    async loadHistory() {
        const container = document.getElementById('download-history');
        if (!container) return;

        try {
            const data = await API.articles.history(100);
            const history = data.history || [];

            if (history.length === 0) {
                container.innerHTML = `
                    <div style="text-align: center; color: var(--text-muted); padding: 20px;">
                        暂无下载历史
                    </div>
                `;
                return;
            }

            container.innerHTML = `
                <div class="history-list">
                    ${history.map(item => {
                        const time = item.time
                            ? new Date(item.time * 1000).toLocaleString('zh-CN')
                            : '';
                        const title = this.escapeHtml(item.title || '');
                        const account = this.escapeHtml(item.account || '');
                        const path = this.escapeHtml(item.path || '');
                        return `
                            <div class="history-item" style="display: flex; align-items: center; justify-content: space-between; gap: 8px;">
                                <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;">
                                    <span class="history-status">${item.success ? '✅' : '❌'}</span>
                                    <span class="history-title" style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${title}">${title}</span>
                                    <span style="font-size: 0.78rem; color: var(--text-muted); flex-shrink: 0;">${account}</span>
                                    <span class="history-time" style="flex-shrink: 0;">${time}</span>
                                </div>
                                <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
                                ${item.success && item.path ? `
                                    <button class="btn btn-secondary btn-sm" data-path="${path}" onclick="HistoryPage.openFile(this.dataset.path)" style="padding: 2px 8px; font-size: 0.75rem;">
                                        打开目录
                                    </button>
                                ` : ''}
                                    <button class="btn btn-danger btn-sm" onclick="HistoryPage.deleteItem(${item._index})" style="padding: 2px 8px; font-size: 0.75rem;">
                                        删除
                                    </button>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>
            `;
        } catch (err) {
            container.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 20px;">加载失败</div>';
        }
    },

    async openFolder() {
        try {
            await API.articles.openFolder();
            Toast.success('下载目录已打开');
        } catch (err) {
            // shown by API
        }
    },

    async openFile(path) {
        if (!path) {
            Toast.warning('无效的路径');
            return;
        }
        try {
            await API.articles.openFile(path);
            Toast.success('正在打开...');
        } catch (err) {
            // shown by API
        }
    },

    async clearHistory() {
        Modal.confirm('清空历史', '确定要清空所有下载历史记录吗？', async () => {
            try {
                await API.articles.clearHistory();
                Toast.success('历史已清空');
                await HistoryPage.loadHistory();
            } catch (err) {
                // shown by API
            }
        });
    },

    async deleteItem(index) {
        Modal.confirm('删除下载', '确定要删除这条记录和对应下载文件吗？', async () => {
            try {
                const result = await API.articles.deleteHistory(index);
                if (result.file_status === 'missing') {
                    Toast.warning(result.message || '下载文件已不存在，已删除记录');
                } else {
                    Toast.success(result.message || '已删除');
                }
                await HistoryPage.loadHistory();
            } catch (err) {
                // shown by API
            }
        });
    },

    escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[char]));
    },
};
