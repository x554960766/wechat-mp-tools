/**
 * 前端 Hash 路由管理器
 */
const Router = {
    routes: {},
    currentPage: null,
    currentKey: null,
    pageCache: {},

    init() {
        // 注册路由
        this.routes = {
            'login': LoginPage,
            'accounts': AccountsPage,
            'articles': ArticlesPage,
            'download': DownloadPage,
            'history': HistoryPage,
            'channels': ChannelsPage,
            'channels_login': typeof ChannelsLoginPage !== 'undefined' ? ChannelsLoginPage : null,
            'channels_accounts': typeof ChannelsAccountsPage !== 'undefined' ? ChannelsAccountsPage : null,
            'channels_history': typeof ChannelsHistoryPage !== 'undefined' ? ChannelsHistoryPage : null,
            'channels_user': typeof ChannelsUserPage !== 'undefined' ? ChannelsUserPage : null,
            'proxy': ProxyPage,
            'settings': SettingsPage,
            'transcode': typeof TranscodePage !== 'undefined' ? TranscodePage : null,
            'dy_transcode': typeof TranscodePage !== 'undefined' ? TranscodePage : null,
            
            // 抖音子系统页面
            'dy_login': typeof DyLoginComponent !== 'undefined' ? DyLoginComponent : null,
            'dy_dashboard': typeof DyDashboardPage !== 'undefined' ? DyDashboardPage : null,
            'dy_search': typeof DySearchPage !== 'undefined' ? DySearchPage : null,
            'dy_user': typeof DyUserPage !== 'undefined' ? DyUserPage : null,
            'dy_parse': typeof DyParsePage !== 'undefined' ? DyParsePage : null,
            'dy_recommend': typeof DyRecommendPage !== 'undefined' ? DyRecommendPage : null,
            'dy_downloads': typeof DyDownloadsPage !== 'undefined' ? DyDownloadsPage : null,
            'dy_liked': typeof DyLikedPage !== 'undefined' ? DyLikedPage : null,
            'dy_collections': typeof DyCollectionsPage !== 'undefined' ? DyCollectionsPage : null,
            
            // 快手子系统页面
            'ks_login': typeof KsLoginComponent !== 'undefined' ? KsLoginComponent : null,
            'ks_user': typeof KsUserPage !== 'undefined' ? KsUserPage : null,
            'ks_parse': typeof KsParsePage !== 'undefined' ? KsParsePage : null,
            'ks_downloads': typeof KsDownloadsPage !== 'undefined' ? KsDownloadsPage : null,

            // 小红书页面
            'xhs_login': typeof XhsLoginPage !== 'undefined' ? XhsLoginPage : null,
            'xhs_accounts': typeof XhsAccountsPage !== 'undefined' ? XhsAccountsPage : null,
            'xhs_notes': typeof XhsNotesPage !== 'undefined' ? XhsNotesPage : null,
            'xhs_download': typeof XhsDownloadPage !== 'undefined' ? XhsDownloadPage : null,
            'xhs_history': typeof XhsHistoryPage !== 'undefined' ? XhsHistoryPage : null,

            // B站页面
            'bili_login': typeof BiliLoginPage !== 'undefined' ? BiliLoginPage : null,
            'bili_accounts': typeof BiliAccountsPage !== 'undefined' ? BiliAccountsPage : null,
            'bili_videos': typeof BiliVideosPage !== 'undefined' ? BiliVideosPage : null,
            'bili_download': typeof BiliDownloadPage !== 'undefined' ? BiliDownloadPage : null,
            'bili_history': typeof BiliHistoryPage !== 'undefined' ? BiliHistoryPage : null,
        };

        // 监听 hash 变化
        window.addEventListener('hashchange', () => this.handleRouting());
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (event) => {
                const page = item.getAttribute('data-page');
                if (page && page === this.currentKey) {
                    event.preventDefault();
                    this.refreshCurrent();
                }
            });
        });

        // 首次加载路由
        this.handleRouting();
    },

    async handleRouting() {
        const hash = window.location.hash.slice(1) || 'login';
        const pageKey = hash.split('?')[0]; // 去掉查询参数
        const page = this.routes[pageKey];

        if (!page) {
            window.location.hash = '#login';
            return;
        }

        if ((pageKey === 'transcode' || pageKey === 'dy_transcode') && App.ffmpegAvailable === false) {
            App.ensureFFmpeg(() => {
                window.location.hash = '#' + pageKey;
            });
            return;
        }

        // 依据当前激活路由，动态切换主题色系 (微信绿色系 vs 抖音暗黑极致灰)
        if (pageKey.startsWith('dy_')) {
            document.body.classList.add('dy-theme');
            document.body.classList.remove('wechat-theme');
        } else if (['login', 'accounts', 'articles', 'download', 'history', 'channels'].includes(pageKey) || pageKey.startsWith('channels_') || pageKey.startsWith('xhs_') || pageKey.startsWith('ks_') || pageKey.startsWith('bili_')) {
            document.body.classList.remove('dy-theme');
            document.body.classList.add('wechat-theme');
        } else if (!document.body.classList.contains('dy-theme') && !document.body.classList.contains('wechat-theme')) {
            // Cold boot on a common page: default to wechat-theme
            document.body.classList.add('wechat-theme');
        }

        // 抖音未登录页面访问限制拦截（dy_downloads 是本地数据，不需要登录）
        const requiresDyLogin = ['dy_dashboard', 'dy_search', 'dy_user', 'dy_recommend', 'dy_liked', 'dy_collections'].includes(pageKey);
        
        if (requiresDyLogin) {
            try {
                // 切换到需要登录的页面时，实时同步最新的登录状态，避免前后台状态不同步导致被误拦截
                await App.checkAuthStatus();
            } catch (err) {
                console.error('Failed to sync auth status before routing:', err);
            }
        }

        const promptEl = document.getElementById('dy-login-prompt-page');
        const hasParams = hash.includes('?');

        // 解析 hash 查询参数，透传给 page.init()
        const routeParams = {};
        if (hasParams) {
            const qs = hash.split('?')[1];
            new URLSearchParams(qs).forEach((v, k) => { routeParams[k] = decodeURIComponent(v); });
        }

        // 含有动态参数的路由（如 ?sec_uid= 或 ?fakeid=）每次重新渲染，避免缓存导致页面内容不更新
        if (hasParams && this.pageCache[pageKey]) {
            if (this.pageCache[pageKey].el) {
                this.pageCache[pageKey].el.remove();
            }
            delete this.pageCache[pageKey];
        }

        if (requiresDyLogin && !App.isDouyinLoggedIn) {
            this.currentPage = null;
            this.currentKey = pageKey;
            this.updateNavUI(pageKey);
            
            const container = document.getElementById('page-container');
            if (container) {
                Array.from(container.children).forEach(child => {
                    if (!child.classList.contains('route-page')) child.remove();
                });
                
                Object.values(this.pageCache).forEach(entry => {
                    if (entry.el) entry.el.style.display = 'none';
                });
                
                if (promptEl) {
                    promptEl.style.display = 'block';
                    container.prepend(promptEl);
                } else {
                    const newPromptEl = document.createElement('div');
                    newPromptEl.id = 'dy-login-prompt-page';
                    newPromptEl.className = 'route-page';
                    newPromptEl.innerHTML = `
                        <div class="empty-state animate-fade-in" style="padding: 80px 24px; text-align: center; max-width: 520px; margin: 60px auto; background: var(--bg-card); border-radius: 16px; border: 1px solid var(--border-color); box-shadow: var(--shadow-lg);">
                            <div class="empty-state-icon" style="color: var(--warning); margin-bottom: 24px;">
                                <svg viewBox="0 0 24 24" fill="none" width="80" height="80" style="display: inline-block;">
                                    <path d="M12 2C6.48 2 2 6.48 2 12C2 17.52 6.48 22 12 22C17.52 22 22 17.52 22 12C22 6.48 17.52 2 12 2ZM12 5C13.66 5 15 6.34 15 8C15 9.66 13.66 11 12 11C10.34 11 9 9.66 9 8C9 6.34 10.34 5 12 5ZM12 19.2C9.5 19.2 7.29 17.92 6 15.98C6.03 13.99 10 12.9 12 12.9C13.99 12.9 17.97 13.99 18 15.98C16.71 17.92 14.5 19.2 12 19.2Z" fill="currentColor"/>
                                </svg>
                            </div>
                            <h3 class="empty-state-title" style="font-size: 1.5rem; font-weight: 700; margin-bottom: 12px; color: var(--text-primary);">该页面需要登录抖音账号</h3>
                            <p class="empty-state-desc" style="color: var(--text-secondary); margin-bottom: 32px; font-size: 0.95rem; line-height: 1.6;">
                                当前您正在访问的页面包含抖音数据，需要有效登录凭证。<br>请先“扫码登录”以解锁全部功能。
                            </p>
                            <div style="display: flex; gap: 12px; justify-content: center;">
                                <button class="btn btn-primary" onclick="Router.navigate('dy_login')" style="padding: 10px 24px; border-radius: 8px; font-weight: 500;">
                                    🔑 扫码登录
                                </button>
                                <button class="btn btn-secondary" onclick="Router.navigate('dy_parse')" style="padding: 10px 24px; border-radius: 8px; font-weight: 500;">
                                    🔗 解析链接
                                </button>
                            </div>
                        </div>
                    `;
                    container.prepend(newPromptEl);
                }
            }
            return;
        } else {
            // 如果已登录或者是不需要登录的页面，隐藏提示卡片
            if (promptEl) {
                promptEl.style.display = 'none';
            }
        }

        this.currentPage = page;
        this.currentKey = pageKey;

        // 更新导航栏激活状态
        this.updateNavUI(pageKey);

        const container = document.getElementById('page-container');
        if (!container) return;

        Array.from(container.children).forEach(child => {
            if (!child.classList.contains('route-page')) child.remove();
        });

        Object.values(this.pageCache).forEach(entry => {
            if (entry.el) entry.el.style.display = 'none';
        });

        if (this.pageCache[pageKey]) {
            this.pageCache[pageKey].el.style.display = 'block';
            container.prepend(this.pageCache[pageKey].el);
            // 命中缓存页：触发可选的 onShow 钩子（用于数据刷新等轻量更新，无需重渲染 DOM）
            const cachedPage = this.pageCache[pageKey].page;
            if (cachedPage && typeof cachedPage.onShow === 'function') {
                try {
                    cachedPage.onShow();
                } catch (err) {
                    console.error('onShow hook error:', err);
                }
            }
            return;
        }

        // 显示加载动画
        const pageEl = document.createElement('div');
        pageEl.className = 'route-page';
        pageEl.dataset.page = pageKey;
        pageEl.innerHTML = `
            <div class="loading-screen">
                <div class="spinner"></div>
                <p>加载中...</p>
            </div>
        `;
        container.prepend(pageEl);

        try {
            // 渲染 HTML
            pageEl.innerHTML = page.render();
            this.pageCache[pageKey] = { page, el: pageEl };

            // 初始化新页面
            if (typeof page.init === 'function') {
                await page.init(routeParams);
            }
        } catch (err) {
            console.error('Routing load error:', err);
            pageEl.innerHTML = `
                <div style="text-align: center; padding: 40px; color: var(--error);">
                    <h3>❌ 页面加载失败</h3>
                    <p style="margin-top: var(--spacing-sm); color: var(--text-muted);">${err.message || err}</p>
                    <button class="btn btn-primary" onclick="Router.handleRouting()" style="margin-top: var(--spacing-md);">重新加载</button>
                </div>
            `;
        }
    },

    async refreshCurrent() {
        if (!this.currentKey) return;
        const cached = this.pageCache[this.currentKey];
        if (!cached) {
            await this.handleRouting();
            return;
        }
        if (cached.page && typeof cached.page.destroy === 'function') {
            try {
                cached.page.destroy();
            } catch (err) {
                console.error('Destroying page error:', err);
            }
        }
        delete this.pageCache[this.currentKey];
        cached.el.remove();
        await this.handleRouting();
    },

    updateNavUI(activeKey) {
        const navItems = document.querySelectorAll('.nav-item');
        navItems.forEach(item => {
            const page = item.getAttribute('data-page');
            if (page === activeKey) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });

        let activeGroup = 'wechat'; // 默认微信
        if (activeKey.startsWith('dy_')) {
            activeGroup = 'douyin';
        } else if (activeKey.startsWith('xhs_')) {
            activeGroup = 'xiaohongshu';
        } else if (activeKey.startsWith('ks_')) {
            activeGroup = 'kuaishou';
        } else if (activeKey.startsWith('bili_')) {
            activeGroup = 'bilibili';
        } else if (activeKey.startsWith('channels_') || activeKey === 'channels') {
            activeGroup = 'wechat_channels';
        } else if (['transcode', 'proxy', 'settings'].includes(activeKey)) {
            activeGroup = 'common';
        }

        const groups = ['wechat', 'wechat_channels', 'douyin', 'xiaohongshu', 'kuaishou', 'bilibili', 'common'];
        groups.forEach(g => {
            const itemsEl = document.getElementById(`items-${g}`);
            const titleEl = document.querySelector(`.nav-group-title[data-group="${g}"]`);
            const arrowEl = titleEl ? titleEl.querySelector('.group-arrow') : null;
            
            if (g === activeGroup) {
                if (itemsEl) {
                    itemsEl.classList.remove('collapsed');
                    if (arrowEl) arrowEl.textContent = '▼';
                }
            } else {
                if (itemsEl) {
                    itemsEl.classList.add('collapsed');
                    if (arrowEl) arrowEl.textContent = '▶';
                }
            }
        });
    },

    navigate(path, params) {
        let hash = '#' + path;
        if (params) {
            const qs = new URLSearchParams(params).toString();
            if (qs) hash += '?' + qs;
        }
        window.location.hash = hash;
    }
};
