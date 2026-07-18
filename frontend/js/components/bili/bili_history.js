/**
 * B站下载历史组件 - 对齐抖音模块下载历史高级交互与分组设计
 */
const BiliHistoryPage = {
    _history: [],
    expandedSource: null,  // 当前展开的来源
    searchKeyword: '',     // 搜索关键词

    render() {
        return `
            <div class="page-header" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h2 class="page-title">B站下载历史</h2>
                    <p class="page-description">查看已下载的哔哩哔哩视频历史记录，支持按UP主分组展示与定位文件。</p>
                </div>
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-secondary" onclick="BiliHistoryPage.openDownloadsFolder()">📂 打开下载文件夹</button>
                    <button class="btn btn-danger" onclick="BiliHistoryPage.clearHistory()">🧹 清空历史</button>
                </div>
            </div>

            <div id="bili-downloads-container">
                <!-- 统计栏 -->
                <div class="card" style="margin-bottom: var(--spacing-lg); padding: 16px 20px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: var(--spacing-md);">
                        <div style="display: flex; gap: var(--spacing-2xl);">
                            <div>
                                <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">已下载视频</span>
                                <strong style="font-size: 1.8rem; color: var(--primary);" id="bili-dl-stat-count">0 个</strong>
                            </div>
                            <div style="border-left: 1px solid var(--border-color); padding-left: var(--spacing-2xl);">
                                <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">来源UP主</span>
                                <strong style="font-size: 1.8rem; color: var(--text-primary);" id="bili-dl-stat-sources">0 个</strong>
                            </div>
                        </div>
                        <div style="display: flex; gap: 8px; align-items: center;">
                            <div style="position: relative;">
                                <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--text-muted);">
                                    <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/>
                                    <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                                </svg>
                                <input type="text" id="bili-dl-search" class="form-input" placeholder="搜索UP主" oninput="BiliHistoryPage.onSearch(this.value)" style="padding-left: 32px; width: 180px; height: 36px; font-size: 0.85rem; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary);">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 空状态 -->
                <div id="bili-downloads-empty" style="display: none; text-align: center; padding: var(--spacing-2xl);">
                    <div style="width: 64px; height: 64px; margin: 0 auto var(--spacing-md); background: rgba(0, 122, 255, 0.08); border-radius: 20px; display: flex; align-items: center; justify-content: center;">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 32px; height: 32px; color: var(--primary);">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <p style="font-size: 1.1rem; margin-bottom: 8px; color: var(--text-primary);">暂无下载历史记录</p>
                    <p style="color: var(--text-muted);">您可以在视频下载或订阅的UP主主页中下载视频</p>
                </div>

                <!-- 来源分组列表 -->
                <div id="bili-downloads-groups" style="display: none; flex-direction: column; gap: var(--spacing-md);"></div>
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
            const data = await API.bili.getHistory();
            this._history = data.history || [];
            this.renderHistory();
        } catch (err) {
            Toast.error('加载历史记录失败: ' + err.message);
        }
    },

    groupBySource() {
        const groups = {};
        this._history.forEach(item => {
            const source = item.source || '未知来源';
            if (!groups[source]) {
                groups[source] = {
                    name: source,
                    items: [],
                    lastTime: ''
                };
            }
            groups[source].items.push(item);
            if (!groups[source].lastTime) {
                groups[source].lastTime = item.time;
            }
        });
        return groups;
    },

    getRelativeTime(timeStr) {
        if (!timeStr) return '';
        try {
            const date = new Date(timeStr.replace(/-/g, '/'));
            const now = new Date();
            const diff = now - date;
            const minutes = Math.floor(diff / 60000);
            const hours = Math.floor(diff / 3600000);
            const days = Math.floor(diff / 86400000);

            if (minutes < 1) return '刚刚';
            if (minutes < 60) return `${minutes} 分钟前`;
            if (hours < 24) return `${hours} 小时前`;
            if (days < 7) return `${days} 天前`;
            return timeStr.split(' ')[0];
        } catch (e) {
            return timeStr;
        }
    },

    renderHistory() {
        const empty = document.getElementById('bili-downloads-empty');
        const groupsContainer = document.getElementById('bili-downloads-groups');
        if (!empty || !groupsContainer) return;

        const total = this._history.length;
        const groups = this.groupBySource();
        const sourceCount = Object.keys(groups).length;

        document.getElementById('bili-dl-stat-count').textContent = total + ' 个';
        document.getElementById('bili-dl-stat-sources').textContent = sourceCount + ' 个';

        if (total === 0) {
            empty.style.display = 'block';
            groupsContainer.style.display = 'none';
            return;
        }

        empty.style.display = 'none';
        groupsContainer.style.display = 'flex';

        // Filter by keyword
        const keyword = this.searchKeyword.toLowerCase().trim();
        const filteredGroups = {};
        Object.keys(groups).forEach(key => {
            if (!keyword || key.toLowerCase().includes(keyword)) {
                filteredGroups[key] = groups[key];
            }
        });

        // Sort by last update time
        const sortedKeys = Object.keys(filteredGroups).sort((a, b) => {
            const timeA = filteredGroups[a].lastTime || '';
            const timeB = filteredGroups[b].lastTime || '';
            return timeB.localeCompare(timeA);
        });

        if (sortedKeys.length === 0) {
            groupsContainer.innerHTML = `
                <div class="card" style="text-align: center; padding: var(--spacing-2xl); color: var(--text-muted);">
                    未找到匹配「${this._esc(keyword)}」的来源UP主
                </div>
            `;
            return;
        }

        groupsContainer.innerHTML = sortedKeys.map(key => {
            const group = filteredGroups[key];
            const isExpanded = this.expandedSource === key;
            const itemCount = group.items.length;
            const relTime = this.getRelativeTime(group.lastTime);

            return `
                <div class="card" style="overflow: hidden; padding: 16px 20px; transition: box-shadow 0.2s ease; margin-bottom: 12px; border: 1px solid var(--border-color); border-radius: 12px; background: var(--bg-card);" 
                     onmouseenter="this.style.boxShadow='0 4px 20px rgba(0,0,0,0.05)'" 
                     onmouseleave="this.style.boxShadow=''">
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
                        <div style="display: flex; align-items: center; gap: var(--spacing-md);">
                            <div style="width: 44px; height: 44px; border-radius: 50%; background: rgba(0, 122, 255, 0.08); display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
                                <svg viewBox="0 0 24 24" fill="none" style="width: 22px; height: 22px; color: var(--primary);">
                                    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                    <circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                </svg>
                            </div>

                            <div style="min-width: 0;">
                                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                                    <span style="font-weight: 600; font-size: 1rem; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${this._esc(group.name)}</span>
                                    <span style="font-size: 0.78rem; color: var(--text-muted); background: var(--bg-tertiary); padding: 2px 6px; border-radius: 4px;">共 ${itemCount} 项</span>
                                </div>
                                <div>
                                    <span style="cursor: pointer; color: var(--primary); font-size: 0.82rem; font-weight: 500; text-decoration: underline;" onclick="BiliHistoryPage.toggleExpand('${this._esc(key).replace(/'/g, "\\'")}')">
                                        ${isExpanded ? '收起详情列表 ↑' : '展开下载详情列表 ↓'}
                                    </span>
                                </div>
                            </div>
                        </div>

                        <div style="display: flex; align-items: center; gap: 16px;">
                            <span style="font-size: 0.78rem; color: var(--text-muted);">${relTime}</span>
                            <div style="display: flex; gap: 8px;">
                                <button class="btn btn-secondary btn-sm" onclick="BiliHistoryPage.openSourceFolder('${this._esc(key).replace(/'/g, "\\'")}')" style="padding: 5px 12px; font-size: 0.82rem; white-space: nowrap;">
                                    📂 打开目录
                                </button>
                                <button class="btn btn-secondary btn-sm" onclick="BiliHistoryPage.toggleExpand('${this._esc(key).replace(/'/g, "\\'")}')" style="padding: 5px 12px; font-size: 0.82rem; white-space: nowrap;">
                                    ${isExpanded ? '隐藏' : '详情'}
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- 展开的详情列表 -->
                    ${isExpanded ? this.renderExpandedItems(group) : ''}
                </div>
            `;
        }).join('');
    },

    renderExpandedItems(group) {
        return `
            <div style="margin-top: var(--spacing-md); border-top: 1px solid var(--border-color); padding-top: var(--spacing-md); overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 0.88rem;">
                    <thead>
                        <tr style="border-bottom: 1px solid var(--border-color);">
                            <th style="padding: 8px 12px; color: var(--text-muted); font-weight: 600;">标题</th>
                            <th style="padding: 8px 12px; color: var(--text-muted); font-weight: 600; width: 80px;">大小</th>
                            <th style="padding: 8px 12px; color: var(--text-muted); font-weight: 600; width: 150px;">下载时间</th>
                            <th style="padding: 8px 12px; color: var(--text-muted); font-weight: 600; width: 140px; text-align: right;">操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${group.items.map(item => {
                            const itemIndex = this._history.indexOf(item);
                            return `
                                <tr style="border-bottom: 1px solid var(--border-color); vertical-align: middle;" onmouseenter="this.style.background='var(--bg-tertiary)'" onmouseleave="this.style.background='transparent'">
                                    <td style="padding: 8px 12px; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                        <span style="font-weight: 500; color: var(--text-primary);" title="${this._esc(item.title)}">${this._esc(item.title)}</span>
                                    </td>
                                    <td style="padding: 8px 12px; color: var(--text-muted);">${item.size || '未知'}</td>
                                    <td style="padding: 8px 12px; color: var(--text-muted); font-size: 0.8rem;">${item.time}</td>
                                    <td style="padding: 8px 12px; text-align: right; white-space: nowrap;">
                                        ${item.path ? `
                                            <button class="btn btn-secondary btn-sm" onclick="BiliHistoryPage.openFile('${item.path.replace(/\\/g, '\\\\')}')" style="padding: 2px 6px; font-size: 0.75rem;">播放</button>
                                            <button class="btn btn-secondary btn-sm" onclick="BiliHistoryPage.openParent('${item.path.replace(/\\/g, '\\\\')}')" style="padding: 2px 6px; font-size: 0.75rem;">定位</button>
                                        ` : ''}
                                        <button class="btn btn-danger btn-sm" onclick="BiliHistoryPage.deleteItem(${itemIndex}, '${this._esc(item.title).replace(/'/g, "\\'")}')" style="padding: 2px 6px; font-size: 0.75rem;">删除</button>
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            </div>
        `;
    },

    toggleExpand(sourceKey) {
        if (this.expandedSource === sourceKey) {
            this.expandedSource = null;
        } else {
            this.expandedSource = sourceKey;
        }
        this.renderHistory();
    },

    onSearch(value) {
        this.searchKeyword = value;
        this.renderHistory();
    },

    async openSourceFolder(sourceName) {
        const groups = this.groupBySource();
        const group = groups[sourceName];
        if (!group || !group.items.length) {
            Toast.error('未找到该来源的下载记录');
            return;
        }

        const firstItem = group.items[0];
        const path = firstItem.path || '';
        const marker = 'bilibili_downloads/';
        const idx = path.indexOf(marker);
        if (idx >= 0) {
            const rest = path.substring(idx + marker.length);
            const parts = rest.split('/');
            if (parts.length > 0) {
                const sourceDir = path.substring(0, idx + marker.length + parts[0].length);
                try {
                    await API.bili.openParent(sourceDir);
                } catch (err) {
                    Toast.error(err.message);
                }
                return;
            }
        }

        try {
            await API.bili.openParent(path);
        } catch (err) {
            Toast.error(err.message);
        }
    },

    async openDownloadsFolder() {
        try {
            await API.bili.openFolder();
        } catch (err) {
            Toast.error('打开文件夹失败: ' + err.message);
        }
    },

    async openFile(path) {
        try {
            await API.bili.openFile(path);
        } catch (err) {
            Toast.error('打开文件失败: ' + err.message);
        }
    },

    async openParent(path) {
        try {
            await API.bili.openParent(path);
        } catch (err) {
            Toast.error('定位父目录失败: ' + err.message);
        }
    },

    deleteItem(index, title) {
        Modal.confirm('删除记录', `确定要删除「${title}」的下载记录及本地文件吗？<br><strong style="color:var(--error);">此操作将连带删除对应的本地下载文件，且不可恢复！</strong>`, async () => {
            try {
                const res = await API.bili.deleteHistory(index);
                Toast.success(res.message);
                await this.loadHistory();
            } catch (err) {
                Toast.error('删除失败: ' + err.message);
            }
        });
    },

    clearHistory() {
        Modal.confirm('清空历史', '确定要清空所有的B站下载记录吗？此操作仅清除记录，不会删除磁盘上的下载视频。', async () => {
            try {
                await API.bili.clearHistory();
                Toast.success('下载历史已清空');
                await this.loadHistory();
            } catch (err) {
                Toast.error('清空历史失败: ' + err.message);
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
