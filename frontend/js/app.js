/**
 * 微信公众号文章下载管理工具 — 全局应用管理器
 */
const App = {
    async init() {
        console.log('App initializing...');

        // 初始化基础组件
        Toast.init();
        Modal.init();

        // 首次加载认证状态
        await this.checkAuthStatus();

        // 初始化路由
        Router.init();

        // 初始化侧边栏手风琴折叠菜单
        this.initSidebarAccordion();

        // 启动定时状态检查 (每 30 秒检查一次登录态)
        setInterval(() => this.checkAuthStatus(), 30000);
    },

    async checkAuthStatus() {
        try {
            // 微信登录状态
            const wechatData = await API.auth.status();
            
            // 抖音登录状态
            const dyRes = await fetch('/api/douyin-auth/status');
            const dyText = await dyRes.text();
            let dyData = {};
            try {
                dyData = JSON.parse(dyText);
            } catch (e) {
                dyData = { logged_in: false };
            }
            
            const prevLoggedIn = this.isDouyinLoggedIn;
            this.isDouyinLoggedIn = !!dyData.logged_in;
            this.douyinAccountInfo = dyData.account_info || null;
            
            this.updateLoginStatus(
                wechatData.logged_in, 
                wechatData.expired || wechatData.may_expired,
                this.isDouyinLoggedIn
            );

            if (this.isDouyinLoggedIn && !prevLoggedIn) {
                const promptEl = document.getElementById('dy-login-prompt-page');
                if (promptEl && promptEl.style.display === 'block') {
                    promptEl.style.display = 'none';
                    Router.refreshCurrent();
                }
            }
        } catch (err) {
            console.error('Failed to fetch auth status:', err);
            this.updateLoginStatus(false, false, false);
        }
    },

    updateLoginStatus(loggedIn, mayExpired = false, dyLoggedIn = false) {
        const wechatDot = document.getElementById('login-status-dot');
        const wechatIndicator = document.getElementById('status-indicator');
        const wechatText = document.getElementById('status-text');

        const dyIndicator = document.getElementById('dy-status-indicator');
        const dyText = document.getElementById('dy-status-text');

        // 更新微信状态显示
        if (loggedIn) {
            if (mayExpired) {
                if (wechatIndicator) wechatIndicator.className = 'status-dot expired';
                if (wechatText) wechatText.textContent = '登录过期';
                if (wechatDot) wechatDot.style.backgroundColor = 'var(--warning)';
            } else {
                if (wechatIndicator) wechatIndicator.className = 'status-dot online';
                if (wechatText) wechatText.textContent = '已登录';
                if (wechatDot) wechatDot.style.backgroundColor = 'var(--success)';
            }
        } else {
            if (wechatIndicator) wechatIndicator.className = 'status-dot offline';
            if (wechatText) wechatText.textContent = '未登录';
            if (wechatDot) wechatDot.style.backgroundColor = 'transparent';
        }

        // 更新抖音状态显示
        if (dyLoggedIn) {
            if (dyIndicator) dyIndicator.className = 'status-dot online';
            if (dyText) dyText.textContent = 'Cookie 已配置';
        } else {
            if (dyIndicator) dyIndicator.className = 'status-dot warning';
            if (dyText) dyText.textContent = '需要登录 Cookie';
        }
    },

    initSidebarAccordion() {
        const headers = document.querySelectorAll('.nav-group-title');
        headers.forEach(header => {
            header.addEventListener('click', () => {
                const group = header.getAttribute('data-group');
                if (group) {
                    this.toggleNavGroup(group);
                }
            });
        });
    },

    toggleNavGroup(targetGroup) {
        const groups = ['wechat', 'wechat_channels', 'douyin', 'common'];
        groups.forEach(g => {
            const itemsEl = document.getElementById(`items-${g}`);
            const titleEl = document.querySelector(`.nav-group-title[data-group="${g}"]`);
            const arrowEl = titleEl ? titleEl.querySelector('.group-arrow') : null;
            
            if (g === targetGroup) {
                if (itemsEl) {
                    const isCollapsed = itemsEl.classList.toggle('collapsed');
                    if (arrowEl) {
                        arrowEl.textContent = isCollapsed ? '▶' : '▼';
                    }
                }
            } else {
                if (itemsEl) {
                    itemsEl.classList.add('collapsed');
                    if (arrowEl) {
                        arrowEl.textContent = '▶';
                    }
                }
            }
        });
    }
};

// 页面加载完成后启动应用
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
