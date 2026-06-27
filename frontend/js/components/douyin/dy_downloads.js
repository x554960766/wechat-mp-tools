const DyDownloadsPage = {
    history: [],
    loading: false,
    expandedSource: null,  // 当前展开的来源
    filterStatus: 'all',   // 筛选状态
    searchKeyword: '',     // 搜索关键词

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
                <!-- 统计栏 -->
                <div class="card" style="margin-bottom: var(--spacing-lg);">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: var(--spacing-md);">
                        <div style="display: flex; gap: var(--spacing-2xl);">
                            <div>
                                <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">已下载项目</span>
                                <strong style="font-size: 1.8rem; color: var(--primary);" id="dy-dl-stat-count">0 个</strong>
                            </div>
                            <div style="border-left: 1px solid var(--border-color); padding-left: var(--spacing-2xl);">
                                <span style="color: var(--text-muted); font-size: 0.85rem; display: block; margin-bottom: 4px;">来源作者</span>
                                <strong style="font-size: 1.8rem; color: var(--text-primary);" id="dy-dl-stat-sources">0 个</strong>
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
                        <div style="display: flex; gap: 8px; align-items: center;">
                            <div style="position: relative;">
                                <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--text-muted);">
                                    <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/>
                                    <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                                </svg>
                                <input type="text" id="dy-dl-search" class="form-input" placeholder="搜索作者" oninput="DyDownloadsPage.onSearch(this.value)" style="padding-left: 32px; width: 180px; height: 36px; font-size: 0.85rem;">
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 加载状态 -->
                <div id="dy-downloads-loading" style="text-align: center; padding: var(--spacing-2xl);">
                    <div class="spinner"></div>
                    <p style="margin-top: var(--spacing-md); color: var(--text-muted);">加载中...</p>
                </div>

                <!-- 空状态 -->
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

                <!-- 来源分组列表 -->
                <div id="dy-downloads-groups" style="display: none; flex-direction: column; gap: var(--spacing-md);"></div>
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

    /**
     * 从 path 中提取来源（兼容旧数据无 source 字段）
     */
    extractSource(item) {
        if (item.source) return item.source;
        const path = item.path || '';
        const marker = 'douyin_downloads/';
        const idx = path.indexOf(marker);
        if (idx >= 0) {
            const rest = path.substring(idx + marker.length);
            const parts = rest.split('/');
            if (parts.length > 0 && parts[0]) return parts[0];
        }
        return '未知来源';
    },

    /**
     * 按来源分组数据
     */
    groupBySource() {
        const groups = {};
        this.history.forEach(item => {
            const source = this.extractSource(item);
            if (!groups[source]) {
                groups[source] = {
                    name: source,
                    items: [],
                    lastTime: '',
                    types: new Set()
                };
            }
            groups[source].items.push(item);
            groups[source].types.add(item.type);
            // 最后下载时间取第一条（history 是按时间倒序的）
            if (!groups[source].lastTime) {
                groups[source].lastTime = item.time;
            }
        });
        return groups;
    },

    /**
     * 获取类型的显示标签
     */
    getTypeLabel(types) {
        const typeSet = types instanceof Set ? types : new Set(types);
        const labels = [];
        if (typeSet.has('视频')) labels.push('视频');
        if (typeSet.has('图文')) labels.push('图文');
        if (typeSet.has('音乐')) labels.push('音乐');
        if (typeSet.has('批量')) labels.push('批量');
        if (typeSet.has('合集')) labels.push('合集');
        return labels.length > 0 ? labels.join(' · ') : '未知';
    },

    /**
     * 获取相对时间显示
     */
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
            return timeStr.split(' ')[0]; // 返回日期部分
        } catch (e) {
            return timeStr;
        }
    },

    renderHistory() {
        const empty = document.getElementById('dy-downloads-empty');
        const groupsContainer = document.getElementById('dy-downloads-groups');

        // 更新统计
        const total = this.history.length;
        const videos = this.history.filter(item => item.type === '视频').length;
        const images = this.history.filter(item => item.type === '图文').length;
        const groups = this.groupBySource();
        const sourceCount = Object.keys(groups).length;

        document.getElementById('dy-dl-stat-count').textContent = total + ' 个';
        document.getElementById('dy-dl-stat-sources').textContent = sourceCount + ' 个';
        document.getElementById('dy-dl-stat-videos').textContent = videos + ' 个';
        document.getElementById('dy-dl-stat-images').textContent = images + ' 个';

        if (total === 0) {
            empty.style.display = 'block';
            groupsContainer.style.display = 'none';
            return;
        }

        empty.style.display = 'none';
        groupsContainer.style.display = 'flex';

        // 按搜索关键词过滤
        const keyword = this.searchKeyword.toLowerCase().trim();
        const filteredGroups = {};
        Object.keys(groups).forEach(key => {
            if (!keyword || key.toLowerCase().includes(keyword)) {
                filteredGroups[key] = groups[key];
            }
        });

        // 按最后下载时间排序
        const sortedKeys = Object.keys(filteredGroups).sort((a, b) => {
            const timeA = filteredGroups[a].lastTime || '';
            const timeB = filteredGroups[b].lastTime || '';
            return timeB.localeCompare(timeA);
        });

        if (sortedKeys.length === 0) {
            groupsContainer.innerHTML = `
                <div class="card" style="text-align: center; padding: var(--spacing-2xl); color: var(--text-muted);">
                    未找到匹配「${this.escapeHtml(keyword)}」的来源作者
                </div>
            `;
            return;
        }

        groupsContainer.innerHTML = sortedKeys.map(key => {
            const group = filteredGroups[key];
            const isExpanded = this.expandedSource === key;
            const itemCount = group.items.length;
            const relTime = this.getRelativeTime(group.lastTime);
            const typesLabel = this.getTypeLabel(group.types);

            // 获取成功下载的数量
            const successCount = itemCount;

            return `
                <div class="card dy-dl-source-card" style="overflow: hidden; transition: box-shadow 0.2s ease;" 
                     onmouseenter="this.style.boxShadow='0 4px 20px rgba(0,0,0,0.08)'" 
                     onmouseleave="this.style.boxShadow=''">
                    <div style="display: flex; align-items: center; gap: var(--spacing-md); padding: 0;">
                        <!-- 来源图标 -->
                        <div style="width: 44px; height: 44px; border-radius: 50%; background: rgba(254, 44, 85, 0.08); display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 22px; height: 22px; color: var(--primary);">
                                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                        </div>

                        <!-- 来源信息 -->
                        <div style="flex: 1; min-width: 0;">
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                                <span style="font-weight: 600; font-size: 1rem; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${this.escapeHtml(group.name)}</span>
                                <span style="font-size: 0.78rem; color: var(--text-muted);">共 ${itemCount} 项</span>
                            </div>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="cursor: pointer; color: var(--primary); font-size: 0.82rem; font-weight: 500;" onclick="DyDownloadsPage.toggleExpand('${this.escapeHtml(key)}')">
                                    查看该来源下载的 ${itemCount} 件作品 →
                                </span>
                            </div>
                        </div>

                        <!-- 状态信息 -->
                        <div style="display: flex; align-items: center; gap: 8px; flex-shrink: 0;">
                            <span style="font-size: 0.78rem; color: var(--text-muted);">${relTime}</span>
                        </div>

                        <!-- 操作按钮 -->
                        <div style="display: flex; align-items: center; gap: 6px; flex-shrink: 0;">
                            <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.openSourceFolder('${this.escapeHtml(key)}')" style="padding: 5px 12px; font-size: 0.82rem; white-space: nowrap;">
                                <svg viewBox="0 0 24 24" fill="none" style="width: 14px; height: 14px; margin-right: 4px; display: inline-block; vertical-align: text-bottom;">
                                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                </svg>
                                打开目录
                            </button>
                            <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.toggleExpand('${this.escapeHtml(key)}')" style="padding: 5px 12px; font-size: 0.82rem; white-space: nowrap;">
                                <svg viewBox="0 0 24 24" fill="none" style="width: 14px; height: 14px; margin-right: 4px; display: inline-block; vertical-align: text-bottom;">
                                    <polyline points="${isExpanded ? '18 15 12 9 6 15' : '6 9 12 15 18 9'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                </svg>
                                查看详情
                            </button>
                        </div>
                    </div>

                    <!-- 展开的详情列表 -->
                    ${isExpanded ? this.renderExpandedItems(group) : ''}
                </div>
            `;
        }).join('');
    },

    /**
     * 渲染展开的来源详情列表
     */
    renderExpandedItems(group) {
        return `
            <div style="margin-top: var(--spacing-md); border-top: 1px solid var(--border-color); padding-top: var(--spacing-md);">
                <div style="overflow-x: auto;">
                    <table style="width: 100%; border-collapse: collapse; text-align: left;">
                        <thead>
                            <tr style="border-bottom: 1px solid var(--border-color);">
                                <th style="padding: 10px var(--spacing-md); color: var(--text-muted); font-weight: 600; font-size: 0.82rem;">标题</th>
                                <th style="padding: 10px var(--spacing-md); color: var(--text-muted); font-weight: 600; font-size: 0.82rem; width: 70px;">类型</th>
                                <th style="padding: 10px var(--spacing-md); color: var(--text-muted); font-weight: 600; font-size: 0.82rem; width: 90px;">大小</th>
                                <th style="padding: 10px var(--spacing-md); color: var(--text-muted); font-weight: 600; font-size: 0.82rem; width: 160px;">下载时间</th>
                                <th style="padding: 10px var(--spacing-md); color: var(--text-muted); font-weight: 600; font-size: 0.82rem; width: 180px; text-align: right;">操作</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${group.items.map((item, idx) => {
                                const itemIndex = this.history.indexOf(item);
                                const typeStyle = item.type === '视频' 
                                    ? 'background: rgba(254, 44, 85, 0.1); color: var(--primary); padding: 3px 7px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;'
                                    : item.type === '图文'
                                    ? 'background: rgba(76, 175, 80, 0.1); color: #4caf50; padding: 3px 7px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;'
                                    : item.type === '音乐'
                                    ? 'background: rgba(33, 150, 243, 0.1); color: #2196f3; padding: 3px 7px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;'
                                    : 'background: rgba(255, 152, 0, 0.1); color: #ff9800; padding: 3px 7px; border-radius: 4px; font-size: 0.75rem; font-weight: 500;';

                                return `
                                    <tr style="border-bottom: 1px solid var(--border-color); vertical-align: middle; transition: background 0.2s;" onmouseenter="this.style.background='var(--bg-glass-hover)'" onmouseleave="this.style.background='transparent'">
                                        <td style="padding: 10px var(--spacing-md); max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                            <span style="font-weight: 500; color: var(--text-primary); font-size: 0.88rem;" title="${this.escapeHtml(item.title)}">${this.escapeHtml(item.title)}</span>
                                        </td>
                                        <td style="padding: 10px var(--spacing-md);">
                                            <span style="${typeStyle}">${item.type}</span>
                                        </td>
                                        <td style="padding: 10px var(--spacing-md); color: var(--text-muted); font-size: 0.85rem;">
                                            ${item.size || '未知'}
                                        </td>
                                        <td style="padding: 10px var(--spacing-md); color: var(--text-muted); font-size: 0.85rem;">
                                            ${item.time}
                                        </td>
                                        <td style="padding: 10px var(--spacing-md); text-align: right; white-space: nowrap;">
                                            <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.openFile('${itemIndex}')" style="padding: 3px 8px; font-size: 0.8rem; margin-right: 4px;">
                                                播放/打开
                                            </button>
                                            <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.openParent('${itemIndex}')" style="padding: 3px 8px; font-size: 0.8rem; margin-right: 4px;">
                                                📂
                                            </button>
                                            ${(item.type === '视频' && App.ffmpegAvailable) ? `
                                            <button class="btn btn-secondary btn-sm" onclick="DyDownloadsPage.importToTranscode('${itemIndex}')" style="padding: 3px 8px; font-size: 0.8rem; background: var(--gradient-primary); color: white;">
                                                转码
                                            </button>
                                            ` : ''}
                                        </td>
                                    </tr>
                                `;
                            }).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    },

    /**
     * 切换展开/折叠来源详情
     */
    toggleExpand(sourceKey) {
        if (this.expandedSource === sourceKey) {
            this.expandedSource = null;
        } else {
            this.expandedSource = sourceKey;
        }
        this.renderHistory();
    },

    /**
     * 搜索筛选
     */
    onSearch(value) {
        this.searchKeyword = value;
        this.renderHistory();
    },

    /**
     * 打开来源作者的目录
     */
    async openSourceFolder(sourceName) {
        // 从该来源的第一个 item 中提取路径
        const groups = this.groupBySource();
        const group = groups[sourceName];
        if (!group || !group.items.length) {
            Toast.show('未找到该来源的下载记录', 'error');
            return;
        }

        const firstItem = group.items[0];
        const path = firstItem.path || '';
        // 提取到来源目录
        const marker = 'douyin_downloads/';
        const idx = path.indexOf(marker);
        if (idx >= 0) {
            const rest = path.substring(idx + marker.length);
            const parts = rest.split('/');
            if (parts.length > 0) {
                const sourceDir = path.substring(0, idx + marker.length + parts[0].length);
                try {
                    await API.douyin.openParent(sourceDir);
                    Toast.show('正在打开目录...', 'info');
                } catch (err) {
                    Toast.show(err.message, 'error');
                }
                return;
            }
        }

        // 回退方案：打开第一个文件的父目录
        try {
            await API.douyin.openParent(path);
            Toast.show('正在打开目录...', 'info');
        } catch (err) {
            Toast.show(err.message, 'error');
        }
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
        Modal.confirm(
            '清空下载历史', 
            '确定要清空抖音下载历史记录，并删除本地所有已下载的视频和图集文件吗？此操作不可恢复！', 
            async () => {
                try {
                    await API.douyin.clearHistory();
                    Toast.show('下载历史和已下载文件已清空', 'success');
                    await DyDownloadsPage.refresh();
                } catch (err) {
                    Toast.show('清空失败: ' + err.message, 'error');
                }
            }
        );
    },

    async refresh() {
        this.expandedSource = null;
        this.searchKeyword = '';
        const searchInput = document.getElementById('dy-dl-search');
        if (searchInput) searchInput.value = '';
        await this.loadHistory();
    },

    showLoading() {
        const loading = document.getElementById('dy-downloads-loading');
        if (loading) loading.style.display = 'block';
        const groups = document.getElementById('dy-downloads-groups');
        if (groups) groups.style.display = 'none';
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
        const groups = document.getElementById('dy-downloads-groups');
        if (groups) groups.style.display = 'none';
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

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    },

    destroy() {
        this.history = [];
        this.expandedSource = null;
        this.searchKeyword = '';
    }
};