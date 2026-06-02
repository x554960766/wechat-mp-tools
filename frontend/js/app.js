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

        // 初始化双系统切换
        this.initSystemSwitcher();

        // 初始化路由
        Router.init();

        // 启动定时状态检查 (每 30 秒检查一次登录态)
        setInterval(() => this.checkAuthStatus(), 30000);
    },

    initSystemSwitcher() {
        const btns = document.querySelectorAll('.sys-btn');
        btns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const sys = e.target.dataset.sys;
                this.switchSystem(sys);
            });
        });
        // 默认初始化微信系统
        this.switchSystem('wechat');
    },

    switchSystem(sys) {
        // 更新按钮状态
        document.querySelectorAll('.sys-btn').forEach(b => {
            b.classList.toggle('active', b.dataset.sys === sys);
        });

        // 切换导航组与Logo可见性
        const isWechat = sys === 'wechat';
        document.getElementById('nav-group-wechat').style.display = isWechat ? 'flex' : 'none';
        document.getElementById('nav-group-douyin').style.display = !isWechat ? 'flex' : 'none';
        
        document.getElementById('logo-wechat').style.display = isWechat ? 'flex' : 'none';
        document.getElementById('logo-douyin').style.display = !isWechat ? 'flex' : 'none';
        
        const commonGroup = document.getElementById('nav-group-common');
        if (commonGroup) commonGroup.style.display = isWechat ? 'block' : 'none';

        // 切换底部状态栏
        const userStatus = document.getElementById('user-status');
        const dyStatus = document.getElementById('dy-status');
        if (userStatus) userStatus.style.display = isWechat ? 'flex' : 'none';
        if (dyStatus) dyStatus.style.display = !isWechat ? 'flex' : 'none';

        // 切换主题样式
        if (!isWechat) {
            document.body.classList.add('dy-theme');
            document.body.classList.remove('wechat-theme');
        } else {
            document.body.classList.remove('dy-theme');
            document.body.classList.add('wechat-theme');
        }

        // 自动导航到默认页面
        if (isWechat) {
            Router.navigate('login');
        } else {
            Router.navigate('dy_dashboard');
        }
    },

    async checkAuthStatus() {
        try {
            // 微信登录状态
            const wechatData = await API.auth.status();
            
            // 抖音登录状态
            const dyRes = await fetch('/api/douyin-auth/status');
            const dyData = await dyRes.json();
            
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
    }
};

// 页面加载完成后启动应用
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
