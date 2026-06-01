/**
 * 前端 Hash 路由管理器
 */
const Router = {
    routes: {},
    currentPage: null,

    init() {
        // 注册路由
        this.routes = {
            'login': LoginPage,
            'accounts': AccountsPage,
            'articles': ArticlesPage,
            'download': DownloadPage,
            'proxy': ProxyPage,
            'settings': SettingsPage,
            
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
        };

        // 监听 hash 变化
        window.addEventListener('hashchange', () => this.handleRouting());

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

        // 销毁上一个页面
        if (this.currentPage && typeof this.currentPage.destroy === 'function') {
            try {
                this.currentPage.destroy();
            } catch (err) {
                console.error('Destroying page error:', err);
            }
        }

        this.currentPage = page;

        // 更新导航栏激活状态
        this.updateNavUI(pageKey);

        const container = document.getElementById('page-container');
        if (!container) return;

        // 显示加载动画
        container.innerHTML = `
            <div class="loading-screen">
                <div class="spinner"></div>
                <p>加载中...</p>
            </div>
        `;

        try {
            // 渲染 HTML
            container.innerHTML = page.render();

            // 初始化新页面
            if (typeof page.init === 'function') {
                await page.init();
            }
        } catch (err) {
            console.error('Routing load error:', err);
            container.innerHTML = `
                <div style="text-align: center; padding: 40px; color: var(--error);">
                    <h3>❌ 页面加载失败</h3>
                    <p style="margin-top: var(--spacing-sm); color: var(--text-muted);">${err.message || err}</p>
                    <button class="btn btn-primary" onclick="Router.handleRouting()" style="margin-top: var(--spacing-md);">重新加载</button>
                </div>
            `;
        }
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
    },

    navigate(path) {
        window.location.hash = '#' + path;
    }
};
