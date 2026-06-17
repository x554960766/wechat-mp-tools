/**
 * 下载历史页面组件 - 优化双栏布局、图片点击放大、视频点击播放及 RSS 订阅功能
 */
const HistoryPage = {
    items: [], // 缓存成功下载的历史记录
    filteredItems: [], // 当前过滤后的记录
    activeIndex: -1, // 当前选择阅读的索引
    uniqueAccounts: [], // 所有唯一的公众号名称

    render() {
        return `
            <style>
                .history-layout {
                    display: flex;
                    gap: 0;
                    margin-top: 16px;
                    border: 1px solid var(--border-color);
                    border-radius: var(--radius-lg);
                    background: var(--bg-card);
                    overflow: hidden;
                    height: calc(100vh - 180px);
                    backdrop-filter: blur(16px);
                    -webkit-backdrop-filter: blur(16px);
                }
                
                .history-left-pane {
                    width: 380px;
                    min-width: 380px;
                    max-width: 380px;
                    border-right: 1px solid var(--border-color);
                    display: flex;
                    flex-direction: column;
                    height: 100%;
                    background: var(--bg-secondary);
                }
                
                .history-left-header {
                    padding: var(--spacing-md);
                    border-bottom: 1px solid var(--border-color);
                    display: flex;
                    flex-direction: column;
                    gap: 10px;
                }
                
                .history-left-header-row {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }
                
                .history-left-title {
                    font-size: 1.1rem;
                    font-weight: 600;
                    color: var(--text-primary);
                }

                .history-filter-select {
                    width: 100%;
                    padding: 8px 12px;
                    background: var(--bg-input);
                    border: 1px solid var(--border-color);
                    border-radius: var(--radius-md);
                    color: var(--text-primary);
                    font-size: 0.85rem;
                    outline: none;
                    cursor: pointer;
                    transition: border-color var(--transition-normal);
                }
                
                .history-filter-select:focus {
                    border-color: var(--border-focus);
                }
                
                .history-article-list {
                    flex: 1;
                    overflow-y: auto;
                    padding: var(--spacing-md);
                    display: flex;
                    flex-direction: column;
                    gap: var(--spacing-md);
                }
                
                .history-date-group {
                    display: flex;
                    flex-direction: column;
                    gap: var(--spacing-sm);
                }
                
                .history-date-header {
                    font-size: 0.8rem;
                    font-weight: 700;
                    color: var(--primary-light);
                    padding: var(--spacing-xs) 0;
                    border-bottom: 1px dashed var(--border-color);
                    text-transform: uppercase;
                    letter-spacing: 0.05em;
                    margin-top: 10px;
                }
                
                .history-article-card {
                    display: flex;
                    gap: 12px;
                    padding: 12px;
                    border-radius: var(--radius-md);
                    background: var(--bg-glass);
                    border: 1px solid var(--border-color);
                    cursor: pointer;
                    transition: all var(--transition-normal);
                    user-select: none;
                }
                
                .history-article-card:hover {
                    background: var(--bg-glass-hover);
                    border-color: var(--border-hover);
                    transform: translateY(-1px);
                }
                
                .history-article-card.active {
                    background: var(--primary-glow);
                    border-color: var(--primary);
                    box-shadow: 0 0 12px var(--primary-glow);
                }
                
                .history-article-info {
                    flex: 1;
                    min-width: 0;
                    display: flex;
                    flex-direction: column;
                    justify-content: space-between;
                }
                
                .history-article-card-title {
                    font-size: 0.9rem;
                    font-weight: 600;
                    color: var(--text-primary);
                    line-height: 1.4;
                    margin-bottom: 4px;
                    display: -webkit-box;
                    -webkit-line-clamp: 2;
                    -webkit-box-orient: vertical;
                    overflow: hidden;
                    transition: color var(--transition-fast);
                }
                
                .history-article-card.active .history-article-card-title {
                    color: var(--primary-light);
                }
                
                .history-article-card-digest {
                    font-size: 0.78rem;
                    color: var(--text-secondary);
                    line-height: 1.4;
                    margin-bottom: 6px;
                    display: -webkit-box;
                    -webkit-line-clamp: 2;
                    -webkit-box-orient: vertical;
                    overflow: hidden;
                }
                
                .history-article-meta {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    font-size: 0.72rem;
                    color: var(--text-muted);
                }

                .history-article-meta-left {
                    display: flex;
                    align-items: center;
                    gap: 6px;
                    min-width: 0;
                }

                .history-article-account {
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                    font-weight: 500;
                }
                
                .history-article-thumbnail {
                    width: 70px;
                    height: 70px;
                    border-radius: var(--radius-sm);
                    overflow: hidden;
                    flex-shrink: 0;
                    background: var(--bg-input);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    border: 1px solid var(--border-color);
                }
                
                .history-article-thumbnail img {
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                }
                
                .history-right-pane {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    height: 100%;
                    background: var(--bg-secondary);
                    min-width: 0; /* 重点优化：防长文本撑爆 flex 容器 */
                }
                
                .history-content-viewer {
                    flex: 1;
                    border: none;
                    width: 100%;
                    height: 0; /* 重点优化：高度设为 0，用 flex-grow 填充，防止底部被裁剪 */
                    background: white;
                }
                
                .history-content-placeholder {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    color: var(--text-muted);
                    gap: var(--spacing-md);
                }
                
                .history-content-placeholder svg {
                    width: 64px;
                    height: 64px;
                    opacity: 0.5;
                }
                
                .history-right-header {
                    padding: 12px var(--spacing-md);
                    border-bottom: 1px solid var(--border-color);
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    background: var(--bg-card);
                }

                /* RSS Modal Custom Styles */
                .rss-modal-content {
                    display: flex;
                    flex-direction: column;
                    gap: 15px;
                    color: var(--text-primary);
                }

                .rss-item-box {
                    background: rgba(255, 255, 255, 0.03);
                    border: 1px solid var(--border-color);
                    border-radius: var(--radius-md);
                    padding: 12px;
                    display: flex;
                    flex-direction: column;
                    gap: 8px;
                }

                .rss-item-title {
                    font-size: 0.9rem;
                    font-weight: 600;
                    color: var(--primary-light);
                }

                .rss-input-group {
                    display: flex;
                    gap: 8px;
                }

                .rss-link-input {
                    flex: 1;
                    padding: 6px 10px;
                    background: var(--bg-input);
                    border: 1px solid var(--border-color);
                    border-radius: var(--radius-sm);
                    color: var(--text-secondary);
                    font-size: 0.8rem;
                    outline: none;
                }

                /* ── 微信主题下消除刺眼绿色 ── */
                body.wechat-theme .history-date-header {
                    color: #576b95 !important;
                }
                body.wechat-theme .history-article-card.active .history-article-card-title {
                    color: #576b95 !important;
                }
                body.wechat-theme .history-article-card.active {
                    background: rgba(87, 107, 149, 0.08) !important;
                    border-color: #576b95 !important;
                    box-shadow: 0 0 12px rgba(87, 107, 149, 0.15) !important;
                }
            </style>
            
            <div class="page-header" style="margin-bottom: 10px;">
                <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                    <div>
                        <h2 class="page-title">下载历史</h2>
                        <p class="page-description">查看已下载的文章记录，并提供 RSS 订阅服务</p>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button class="btn btn-secondary btn-sm" onclick="HistoryPage.openFolder()">打开下载目录</button>
                        <button class="btn btn-primary btn-sm" onclick="HistoryPage.showRssModal()" style="background: var(--gradient-primary); color: white; display: inline-flex; align-items: center;">
                            <svg style="width: 14px; height: 14px; margin-right: 6px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg>
                            RSS 订阅
                        </button>
                        <button class="btn btn-secondary btn-sm" onclick="HistoryPage.clearHistory()">清空历史</button>
                    </div>
                </div>
            </div>
            
            <div class="history-layout">
                <!-- 左侧：文章列表 -->
                <div class="history-left-pane">
                    <div class="history-left-header">
                        <div class="history-left-header-row">
                            <span class="history-left-title">文章列表</span>
                            <span id="history-total-badge" class="badge badge-info" style="font-size: 0.72rem; padding: 2px 6px;">共 0 篇</span>
                        </div>
                        <select id="history-account-filter" class="history-filter-select" onchange="HistoryPage.filterHistory(this.value)">
                            <option value="">全部公众号</option>
                        </select>
                    </div>
                    <div id="history-list-container" class="history-article-list">
                        <!-- 加载指示 -->
                        <div class="loading-screen" style="min-height: 100px;">
                            <div class="spinner"></div>
                        </div>
                    </div>
                </div>
                
                <!-- 右侧：正文查看器 -->
                <div id="history-viewer-container" class="history-right-pane">
                    <div class="history-content-placeholder">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
                            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
                        </svg>
                        <p>请在左侧选择一篇文章进行阅读</p>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        this.activeIndex = -1;
        this.activeArticlePath = null;
        await this.loadHistory();
    },

    onShow() {
        // 刷新历史数据并保留当前选中状态 (若有)
        this.loadHistory();
    },

    async loadHistory() {
        try {
            const data = await API.articles.history(999999);
            const history = data.history || [];

            // 过滤成功且包含可用文件路径的条目，或者永久性失败的条目（比如作者已删除/内容被屏蔽/链接已过期）
            this.items = history.filter(item => 
                (item.success && item.path) || 
                (!item.success && item.error && (item.error.includes("删除") || item.error.includes("屏蔽") || item.error.includes("过期")))
            );
            
            // 按文章的发布时间（或下载时间）进行降序排序（最新发布的在前）
            this.items.sort((a, b) => {
                const timeA = a.publish_time || a.time || 0;
                const timeB = b.publish_time || b.time || 0;
                return timeB - timeA;
            });
            
            // 提取所有唯一的公众号名称
            this.uniqueAccounts = [...new Set(this.items.map(item => item.account).filter(Boolean))];

            this.updateAccountFilter();
            this.filterHistory(document.getElementById('history-account-filter')?.value || "");

        } catch (err) {
            console.error('加载历史记录失败:', err);
            const container = document.getElementById('history-list-container');
            if (container) {
                container.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 20px;">加载失败</div>';
            }
        }
    },

    updateAccountFilter() {
        const select = document.getElementById('history-account-filter');
        if (!select) return;

        const currentValue = select.value;
        select.innerHTML = '<option value="">全部公众号</option>';
        
        this.uniqueAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account;
            option.textContent = account;
            if (account === currentValue) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    },

    filterHistory(account) {
        if (account) {
            this.filteredItems = this.items.filter(item => item.account === account);
        } else {
            this.filteredItems = [...this.items];
        }

        // 更新徽章数量
        const badge = document.getElementById('history-total-badge');
        if (badge) {
            badge.textContent = `共 ${this.filteredItems.length} 篇`;
        }

        this.renderList();
    },

    renderList() {
        const container = document.getElementById('history-list-container');
        if (!container) return;

        if (this.filteredItems.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; color: var(--text-muted); padding: 20px;">
                    暂无下载记录
                </div>
            `;
            // 若无内容，重置右侧查看器
            const viewer = document.getElementById('history-viewer-container');
            if (viewer) {
                viewer.innerHTML = `
                    <div class="history-content-placeholder">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
                            <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
                        </svg>
                        <p>暂无文章内容</p>
                    </div>
                `;
            }
            return;
        }

        // 按发布日期或下载日期分组
        const groups = {};
        this.filteredItems.forEach((item, index) => {
            const timestamp = item.publish_time || item.time || 0;
            const dateStr = this.getLocalDateString(timestamp);
            if (!groups[dateStr]) {
                groups[dateStr] = [];
            }
            groups[dateStr].push({ item, index });
        });

        // 渲染分组的 HTML
        let html = '';
        Object.keys(groups).forEach(dateStr => {
            html += `
                <div class="history-date-group">
                    <div class="history-date-header">${dateStr}</div>
                    ${groups[dateStr].map(({ item, index }) => {
                        const title = this.escapeHtml(item.title || '无标题');
                        const digest = this.escapeHtml(item.digest || '暂无描述');
                        const account = this.escapeHtml(item.account || '未知公众号');
                        const timestamp = item.publish_time || item.time || 0;
                        const timeStr = this.getRelativeTimeString(timestamp);
                        const isActive = this.activeIndex === index ? 'active' : '';
                        
                        // 微信图片防盗链已经通过 no-referrer 处理
                        const coverHtml = item.cover_url ? `
                            <div class="history-article-thumbnail">
                                <img src="${item.cover_url}" alt="cover" onerror="this.parentNode.style.display='none'">
                            </div>
                        ` : '';

                        const statusBadge = !item.success ? `<span class="badge badge-error" style="font-size: 0.7rem; margin-left: 6px;">${this.escapeHtml(item.error || '失败')}</span>` : '';

                        return `
                            <div class="history-article-card ${isActive}" onclick="HistoryPage.selectArticle(${index})">
                                <div class="history-article-info">
                                    <div class="history-article-card-title" title="${title}">${title}${statusBadge}</div>
                                    <div class="history-article-card-digest">${digest}</div>
                                    <div class="history-article-meta">
                                        <div class="history-article-meta-left">
                                            <span class="history-article-account" title="${account}">${account}</span>
                                        </div>
                                        <span class="history-article-time">${timeStr}</span>
                                    </div>
                                </div>
                                ${coverHtml}
                            </div>
                        `;
                    }).join('')}
                </div>
            `;
        });

        container.innerHTML = html;

        // 根据之前记录的 path 寻找其在新列表中的 index
        if (this.activeArticlePath) {
            const foundIdx = this.filteredItems.findIndex(item => item.path === this.activeArticlePath);
            if (foundIdx !== -1) {
                this.activeIndex = foundIdx;
            } else {
                this.activeIndex = -1;
                this.activeArticlePath = null;
            }
        }

        // 若之前有选中，保留右侧页面；否则若第一次加载，默认选中第一篇
        if (this.activeIndex >= 0 && this.activeIndex < this.filteredItems.length) {
            this.selectArticle(this.activeIndex, false);
        } else if (this.filteredItems.length > 0) {
            this.selectArticle(0, true);
        }
    },

    selectArticle(index, forceReload = true) {
        this.activeIndex = index;
        const item = this.filteredItems[index];
        if (!item) return;

        this.activeArticlePath = item.path;

        // 切换左侧 active 样式
        const cards = document.querySelectorAll('.history-article-card');
        cards.forEach((card, idx) => {
            if (idx === index) {
                card.classList.add('active');
            } else {
                card.classList.remove('active');
            }
        });

        if (!forceReload) return;

        const viewer = document.getElementById('history-viewer-container');
        if (!viewer) return;

        const title = this.escapeHtml(item.title || '文章正文');
        const path = this.escapeHtml(item.path || '');
        const origUrl = item.link || '';

        if (!item.success) {
            viewer.innerHTML = `
                <div class="history-right-header">
                    <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; padding-right: 15px;">
                        <div style="font-size: 0.95rem; font-weight: 600; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${title}">${title}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${origUrl}</div>
                    </div>
                    <div style="display: flex; gap: 6px; flex-shrink: 0;">
                        ${origUrl ? `
                            <button class="btn btn-secondary btn-sm" onclick="window.open('${origUrl}', '_blank')" style="padding: 4px 10px; font-size: 0.78rem;">
                                原文链接
                            </button>
                        ` : ''}
                        <button class="btn btn-danger btn-sm" onclick="HistoryPage.deleteItem(${item._index})" style="padding: 4px 10px; font-size: 0.78rem;">
                            删除
                        </button>
                    </div>
                </div>
                <div class="history-content-placeholder" style="color: var(--error); padding: 40px; text-align: center;">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 64px; height: 64px; margin-bottom: var(--spacing-md); opacity: 0.8;">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="12" y1="8" x2="12" y2="12"/>
                        <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    <p style="font-size: 1.1rem; font-weight: 600;">${this.escapeHtml(item.error || '下载失败')}</p>
                    <p style="font-size: 0.85rem; color: var(--text-muted); margin-top: var(--spacing-sm);">该微信文章目前已不可用，详情如上提示。</p>
                </div>
            `;
            return;
        }
        
        // 只有视频才能导入转码（判断后缀）
        const isVideo = path.toLowerCase().endsWith('.mp4') || 
                        path.toLowerCase().endsWith('.mov') || 
                        path.toLowerCase().endsWith('.mkv') || 
                        path.toLowerCase().endsWith('.avi') || 
                        path.toLowerCase().endsWith('.webm');
        
        const serveUrl = this.buildArticleServeUrl(item);

        viewer.innerHTML = `
            <div class="history-right-header">
                <div style="display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; padding-right: 15px;">
                    <div style="font-size: 0.95rem; font-weight: 600; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${title}">${title}</div>
                    <div style="font-size: 0.75rem; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${path}</div>
                </div>
                <div style="display: flex; gap: 6px; flex-shrink: 0;">
                    ${origUrl ? `
                        <button class="btn btn-secondary btn-sm" onclick="window.open('${origUrl}', '_blank')" style="padding: 4px 10px; font-size: 0.78rem;">
                            原文链接
                        </button>
                    ` : ''}
                    <button class="btn btn-secondary btn-sm" data-path="${path}" onclick="HistoryPage.openFile(this.dataset.path)" style="padding: 4px 10px; font-size: 0.78rem;">
                        打开文件夹
                    </button>
                    ${(isVideo && App.ffmpegAvailable) ? `
                    <button class="btn btn-secondary btn-sm" data-path="${path}" data-title="${title}" onclick="HistoryPage.importToTranscode(this.dataset.path, this.dataset.title)" style="padding: 4px 10px; font-size: 0.78rem; background: var(--gradient-primary); color: white;">
                        导入转码
                    </button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="HistoryPage.deleteItem(${item._index})" style="padding: 4px 10px; font-size: 0.78rem;">
                        删除
                    </button>
                </div>
            </div>
            <iframe class="history-content-viewer" src="${serveUrl}" onload="HistoryPage.onIframeLoad(this)"></iframe>
        `;
    },

    buildArticleServeUrl(item) {
        const title = item.title || 'article';
        const rawPath = String(item.path || '').replace(/\\/g, '/');
        const marker = '/articles_full/';
        const markerIndex = rawPath.lastIndexOf(marker);
        let relativeDir = '';

        if (markerIndex >= 0) {
            relativeDir = rawPath.slice(markerIndex + marker.length);
        } else {
            const parts = rawPath.split('/').filter(Boolean);
            const dirName = parts.length ? parts[parts.length - 1] : title;
            relativeDir = [item.account || '', dirName].filter(Boolean).join('/');
        }

        const parts = relativeDir.split('/');
        const folderName = parts[parts.length - 1] || title;
        const filename = `${folderName}.html`;

        const encodedDir = relativeDir
            .split('/')
            .filter(Boolean)
            .map(segment => encodeURIComponent(segment))
            .join('/');
        return `/api/articles/serve-file/${encodedDir}/${encodeURIComponent(filename)}`;
    },

    /**
     * 当内嵌 iframe 加载完毕时运行
     * 1. 注入图片灯箱脚本，点击放大显示图片
     * 2. 视频点击能直接播放/暂停
     */
    onIframeLoad(iframe) {
        try {
            const doc = iframe.contentDocument || iframe.contentWindow.document;
            if (!doc) return;

            // 注入样式，解决被裁剪和防盗链等导致的显示问题，并添加缩放、视频指针
            const style = doc.createElement('style');
            style.textContent = `
                html, body {
                    max-width: 100% !important;
                    overflow-x: hidden !important;
                    box-sizing: border-box !important;
                    height: auto !important;
                    overflow-y: auto !important;
                }
                body {
                    padding: 24px !important;
                    margin: 0 auto !important;
                    background-color: #ffffff !important;
                    color: #222222 !important;
                    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif !important;
                }
                #js_article, #page-content, .rich_media_area_primary, .rich_media_content, #js_content {
                    max-width: 100% !important;
                    width: 100% !important;
                    box-sizing: border-box !important;
                    padding: 0 !important;
                    margin: 0 auto !important;
                    visibility: visible !important;
                }
                /* 图片样式，手势放大 */
                img {
                    cursor: zoom-in;
                    max-width: 100% !important;
                    height: auto !important;
                    display: block;
                    margin: 12px auto;
                    transition: opacity 0.2s ease;
                }
                img:hover {
                    opacity: 0.92;
                }
                /* 视频样式，手势播放 */
                video {
                    cursor: pointer;
                    max-width: 100% !important;
                    height: auto !important;
                    display: block;
                    margin: 12px auto;
                }
                /* 灯箱遮罩层 */
                .lightbox-overlay {
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(10, 10, 20, 0.95);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    z-index: 999999;
                    cursor: zoom-out;
                    opacity: 0;
                    transition: opacity 0.2s ease;
                }
                .lightbox-overlay.active {
                    opacity: 1;
                }
                /* 灯箱内部大图 */
                .lightbox-img {
                    max-width: 95%;
                    max-height: 95%;
                    object-fit: contain;
                    border-radius: 6px;
                    box-shadow: 0 10px 40px rgba(0,0,0,0.85);
                    transform: scale(0.95);
                    transition: transform 0.25s cubic-bezier(0.1, 0.8, 0.25, 1);
                    cursor: zoom-out;
                }
                .lightbox-overlay.active .lightbox-img {
                    transform: scale(1);
                }
            `;
            doc.head.appendChild(style);

            // 监听图片点击事件（放大）
            doc.body.addEventListener('click', (e) => {
                const target = e.target;
                if (target.tagName === 'IMG') {
                    // 如果已经是灯箱中的图片，则由遮罩层处理，避免重复
                    if (target.classList.contains('lightbox-img')) return;

                    e.preventDefault();
                    e.stopPropagation();

                    const imgSrc = target.src;
                    const overlay = doc.createElement('div');
                    overlay.className = 'lightbox-overlay';
                    overlay.innerHTML = `<img class="lightbox-img" src="${imgSrc}">`;
                    
                    // 锁定背景滚动
                    doc.body.style.overflow = 'hidden';
                    doc.body.appendChild(overlay);

                    // 动画激活动作
                    setTimeout(() => overlay.classList.add('active'), 20);

                    // 点击遮罩退回并解锁背景滚动
                    overlay.addEventListener('click', () => {
                        overlay.classList.remove('active');
                        doc.body.style.overflow = '';
                        setTimeout(() => overlay.remove(), 200);
                    });
                }
            });

            // 监听视频点击直接播放/暂停（过滤掉下方控制条区域以防冲突）
            doc.body.addEventListener('click', (e) => {
                const target = e.target;
                if (target.tagName === 'VIDEO') {
                    const rect = target.getBoundingClientRect();
                    const clickY = e.clientY - rect.top;
                    // 如果点击在底部 50px（通常是控制条），则交由浏览器原生控件处理
                    if (rect.height - clickY < 50) {
                        return;
                    }
                    e.preventDefault();
                    e.stopPropagation();
                    if (target.paused) {
                        target.play().catch(err => console.log('视频自动播放失败:', err));
                    } else {
                        target.pause();
                    }
                }
            });

            // 拦截所有 <a> 标签的点击事件，使其在外部浏览器打开，避免 iframe 内部跳转
            doc.body.addEventListener('click', (e) => {
                const a = e.target.closest('a');
                if (a) {
                    const href = a.getAttribute('href');
                    if (href && (href.startsWith('http') || href.startsWith('//'))) {
                        e.preventDefault();
                        e.stopPropagation();
                        // 优先使用父窗口 (主页面) 的 window.open 以确保在 pywebview 中能正确调起系统默认浏览器
                        if (window.parent && window.parent !== window) {
                            window.parent.open(a.href, '_blank');
                        } else {
                            window.open(a.href, '_blank');
                        }
                    }
                }
            }, true);

        } catch (err) {
            console.error('Iframe 样式注入或交互绑定失败(跨域或未加载完成):', err);
        }
    },

    showRssModal() {
        const baseOrigin = window.location.origin;
        const globalRss = `${baseOrigin}/api/articles/rss`;
        
        let accountRssHtml = '';
        if (this.uniqueAccounts.length > 0) {
            accountRssHtml = `
                <div style="margin-top: 10px; font-size: 0.85rem; font-weight: 600; color: var(--text-secondary);">按公众号分源订阅：</div>
                ${this.uniqueAccounts.map(account => {
                    const accRss = `${baseOrigin}/api/articles/rss/${encodeURIComponent(account)}`;
                    return `
                        <div class="rss-item-box">
                            <span class="rss-item-title">${this.escapeHtml(account)}</span>
                            <div class="rss-input-group">
                                <input type="text" readonly class="rss-link-input" value="${accRss}">
                                <button class="btn btn-primary btn-sm" onclick="HistoryPage.copyText('${accRss}')" style="padding: 2px 10px; font-size: 0.75rem;">复制</button>
                            </div>
                        </div>
                    `;
                }).join('')}
            `;
        }

        const content = `
            <div class="rss-modal-content">
                <p style="color: var(--text-secondary); font-size: 0.85rem; line-height: 1.5;">
                    您可以将离线下载的文章转换为标准的 RSS 2.0 订阅。直接在阅读器（如 Follow、NetNewsWire、Reeder、TTRSS 等）中添加以下链接，即可实时同步离线文章：
                </p>
                <div class="rss-item-box" style="margin-top: 5px;">
                    <span class="rss-item-title" style="color: var(--primary-light);">全局合并源（全部已下载的文章）</span>
                    <div class="rss-input-group">
                        <input type="text" readonly class="rss-link-input" value="${globalRss}" style="border-color: rgba(102, 126, 234, 0.3);">
                        <button class="btn btn-primary btn-sm" onclick="HistoryPage.copyText('${globalRss}')" style="padding: 2px 10px; font-size: 0.75rem;">复制</button>
                    </div>
                </div>
                ${accountRssHtml}
            </div>
        `;

        Modal.open({
            title: 'RSS 订阅服务',
            content: content,
            footer: '<button class="btn btn-secondary btn-sm" onclick="Modal.close()">关闭</button>'
        });
    },

    copyText(text) {
        navigator.clipboard.writeText(text).then(() => {
            Toast.success('订阅链接已复制');
        }).catch(err => {
            Toast.error('复制失败，请手动选择复制');
        });
    },

    getLocalDateString(timestamp) {
        const d = new Date(timestamp * 1000);
        const today = new Date();
        const yesterday = new Date();
        yesterday.setDate(today.getDate() - 1);
        
        if (d.toDateString() === today.toDateString()) {
            return "今天";
        } else if (d.toDateString() === yesterday.toDateString()) {
            return "昨天";
        } else {
            const year = d.getFullYear();
            const month = d.getMonth() + 1;
            const day = d.getDate();
            return `${year}年${month}月${day}日`;
        }
    },
    
    getRelativeTimeString(timestamp) {
        const diff = Math.floor(Date.now() / 1000) - timestamp;
        if (diff < 60) {
            return "刚刚";
        } else if (diff < 3600) {
            return `${Math.floor(diff / 60)}分钟前`;
        } else if (diff < 86400) {
            return `${Math.floor(diff / 3600)}小时前`;
        } else if (diff < 2592000) {
            return `${Math.floor(diff / 86400)}天前`;
        } else {
            const d = new Date(timestamp * 1000);
            return `${d.getMonth() + 1}月${d.getDate()}日`;
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
        Modal.confirm('清空历史', '确定要清空所有公众号下载历史记录吗？', () => {
            Modal.confirm('确认清空', '此操作将永久删除所有公众号下载历史记录，且不可恢复！确定要继续吗？', async () => {
                try {
                    await API.articles.clearHistory();
                    Toast.success('历史已清空');
                    await HistoryPage.loadHistory();
                } catch (err) {
                    // shown by API
                }
            });
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
                // 重置当前活跃索引并刷新
                HistoryPage.activeIndex = -1;
                HistoryPage.activeArticlePath = null;
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
    
    async importToTranscode(path, title) {
        if (!path) {
            Toast.warning('无效的视频或文章路径');
            return;
        }
        
        Toast.info('正在解析视频路径...');
        try {
            const res = await API.post('/api/transcode/resolve-path', { path });
            if (res && res.success && res.path) {
                Toast.success('解析成功，正在跳转到转码页面...');
                setTimeout(() => {
                    Router.navigate(`transcode?path=${encodeURIComponent(res.path)}&name=${encodeURIComponent(res.name)}`);
                }, 500);
            } else {
                Toast.error(res.error || '该下载未包含支持的视频文件');
            }
        } catch (err) {
            Toast.error(err.message || '解析视频路径失败，可能不包含视频文件');
        }
    },
};
