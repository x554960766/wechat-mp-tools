/**
 * 扫码登录页面组件
 */
const LoginPage = {
    _pollTimer: null,

    formatDate(timestamp) {
        return timestamp
            ? new Date(timestamp * 1000).toLocaleString('zh-CN')
            : '未知';
    },

    formatRemaining(seconds) {
        if (!seconds || seconds <= 0) return '已过期';
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor((seconds % 86400) / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        if (days > 0) return `${days}天 ${hours}小时`;
        if (hours > 0) return `${hours}小时 ${minutes}分钟`;
        return `${Math.max(1, minutes)}分钟`;
    },

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">扫码登录</h2>
                <p class="page-description">使用微信扫描二维码登录公众平台后台，获取操作权限</p>
            </div>

            <div class="login-container">
                <div class="login-illustration">
                    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <rect x="3" y="3" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.5"/>
                        <rect x="14" y="3" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.5"/>
                        <rect x="3" y="14" width="7" height="7" rx="1" stroke="currentColor" stroke-width="1.5"/>
                        <rect x="14" y="14" width="3" height="3" stroke="currentColor" stroke-width="1.5"/>
                        <rect x="18" y="14" width="3" height="3" stroke="currentColor" stroke-width="1.5"/>
                        <rect x="14" y="18" width="3" height="3" stroke="currentColor" stroke-width="1.5"/>
                        <rect x="18" y="18" width="3" height="3" stroke="currentColor" stroke-width="1.5"/>
                        <rect x="5" y="5" width="3" height="3" fill="currentColor" opacity="0.4"/>
                        <rect x="16" y="5" width="3" height="3" fill="currentColor" opacity="0.4"/>
                        <rect x="5" y="16" width="3" height="3" fill="currentColor" opacity="0.4"/>
                    </svg>
                </div>

                <div id="login-action-area">
                    <button class="btn btn-primary btn-lg" id="btn-start-login" onclick="LoginPage.startLogin()">
                        <svg viewBox="0 0 24 24" fill="none" width="20" height="20">
                            <path d="M15 3H19C20.1 3 21 3.9 21 5V19C21 20.1 20.1 21 19 21H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <polyline points="10,17 15,12 10,7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <line x1="15" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        显示登录二维码
                    </button>
                    <p style="color: var(--text-muted); font-size: 0.85rem; margin-top: 12px;">
                        点击后将在下方直接加载登录二维码，使用微信扫描即可完成登录
                    </p>
                </div>

                <div class="login-status-card" id="login-status-card">
                    <div id="login-status-content">
                        <!-- 动态内容 -->
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        await this.refreshStatus();
    },

    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async refreshStatus() {
        try {
            const data = await API.auth.status();
            this.updateStatusUI(data);
        } catch (err) {
            // 静默处理
        }
    },

    updateStatusUI(data) {
        const container = document.getElementById('login-status-content');
        const actionArea = document.getElementById('login-action-area');
        if (!container) return;

        if (data.logged_in) {
            const saveTime = this.formatDate(data.save_time);
            const expiresAt = this.formatDate(data.expires_at);
            const remaining = this.formatRemaining(data.remaining_seconds);

            container.innerHTML = `
                <div class="login-info-row">
                    <span class="login-info-label">状态</span>
                    <span class="badge badge-success">已登录</span>
                </div>
                <div class="login-info-row">
                    <span class="login-info-label">Token</span>
                    <span class="login-info-value" style="font-family: monospace;">${data.token_preview || '-'}</span>
                </div>
                <div class="login-info-row">
                    <span class="login-info-label">登录时间</span>
                    <span class="login-info-value">${saveTime}</span>
                </div>
                <div class="login-info-row">
                    <span class="login-info-label">到期时间</span>
                    <span class="login-info-value">${expiresAt}</span>
                </div>
                <div class="login-info-row">
                    <span class="login-info-label">剩余时间</span>
                    <span class="login-info-value" style="color: var(--error); font-weight: 600;">${remaining}</span>
                </div>
                <div class="login-info-row">
                    <span class="login-info-label">提示</span>
                    <span class="login-info-value">${data.message}</span>
                </div>
                <div style="margin-top: 16px; display: flex; gap: 8px; justify-content: center;">
                    <button class="btn btn-secondary" onclick="LoginPage.checkCredentials()">
                        验证凭证
                    </button>
                    <button class="btn btn-secondary" onclick="LoginPage.startLogin()">
                        重新登录
                    </button>
                    <button class="btn btn-danger btn-sm" onclick="LoginPage.logout()">
                        退出登录
                    </button>
                </div>
            `;

            // 更新全局状态
            App.updateLoginStatus(true, data.may_expired);

        } else {
            const loginState = data.login_state || {};
            if (data.expired) {
                const saveTime = this.formatDate(data.save_time);
                const expiresAt = this.formatDate(data.expires_at);

                container.innerHTML = `
                    <div style="text-align: center;">
                        <p style="color: var(--warning); font-weight: 600; margin-bottom: 12px;">登录已过期，请重新扫码登录</p>
                        <div class="login-info-row">
                            <span class="login-info-label">上次登录</span>
                            <span class="login-info-value">${saveTime}</span>
                        </div>
                        <div class="login-info-row">
                            <span class="login-info-label">到期时间</span>
                            <span class="login-info-value">${expiresAt}</span>
                        </div>
                        <button class="btn btn-primary" style="margin-top: 16px;" onclick="LoginPage.startLogin()">
                            重新登录
                        </button>
                    </div>
                `;
            } else if (loginState.status === 'scanning') {
                container.innerHTML = `
                    <div style="text-align: center; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 10px 0;">
                        ${loginState.qrcode ? `
                            <div class="qrcode-wrapper" style="background: white; padding: 12px; border-radius: 12px; border: 1px solid var(--border-color); box-shadow: var(--shadow-lg); margin-bottom: 16px; transition: all 0.3s ease; display: inline-block;">
                                <img src="data:image/png;base64,${loginState.qrcode}" style="width: 200px; height: 200px; display: block; border-radius: 8px;" />
                            </div>
                        ` : `
                            <div class="spinner" style="margin: 0 auto 16px;"></div>
                        `}
                        <p style="color: var(--text-primary); font-weight: 600; margin-bottom: 8px;">${loginState.message}</p>
                        <div class="progress-bar" style="width: 80%; margin: 8px auto 0;">
                            <div class="progress-fill" style="width: ${loginState.progress}%"></div>
                        </div>
                    </div>
                `;
            } else if (loginState.status === 'failed') {
                container.innerHTML = `
                    <div style="text-align: center;">
                        <p style="color: var(--error); font-weight: 600;">❌ ${loginState.message}</p>
                        <button class="btn btn-primary" style="margin-top: 12px;" onclick="LoginPage.startLogin()">
                            重新尝试
                        </button>
                    </div>
                `;
            } else if (loginState.status === 'success') {
                container.innerHTML = `
                    <div style="text-align: center;">
                        <p style="color: var(--success); font-weight: 600;">✅ ${loginState.message}</p>
                    </div>
                `;
                setTimeout(() => this.refreshStatus(), 500);
            } else {
                container.innerHTML = `
                    <div style="text-align: center; color: var(--text-muted);">
                        <p>尚未登录，请点击上方按钮开始扫码</p>
                    </div>
                `;
            }

            App.updateLoginStatus(false);
        }
    },

    async startLogin() {
        const btn = document.getElementById('btn-start-login');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner" style="width: 18px; height: 18px; border-width: 2px;"></div> 正在启动...';
        }

        try {
            await API.auth.login();
            Toast.info('已启动登录流程，正在获取登录二维码...');

            // 开始轮询状态
            if (this._pollTimer) clearInterval(this._pollTimer);
            this._pollTimer = setInterval(async () => {
                await this.refreshStatus();
                const data = await API.auth.status();
                if (data.logged_in || data.login_state?.status === 'failed') {
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    if (btn) {
                        btn.disabled = false;
                        btn.innerHTML = `
                            <svg viewBox="0 0 24 24" fill="none" width="20" height="20">
                                <path d="M15 3H19C20.1 3 21 3.9 21 5V19C21 20.1 20.1 21 19 21H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <polyline points="10,17 15,12 10,7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <line x1="15" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            显示登录二维码
                        `;
                    }
                }
            }, 2000);

        } catch (err) {
            Toast.error('启动登录失败: ' + err.message);
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" width="20" height="20">
                        <path d="M15 3H19C20.1 3 21 3.9 21 5V19C21 20.1 20.1 21 19 21H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="10,17 15,12 10,7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <line x1="15" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    显示登录二维码
                `;
            }
        }
    },

    async checkCredentials() {
        Toast.info('正在验证凭证...');
        try {
            const data = await API.auth.checkCredentials();
            if (data.valid) {
                Toast.success(data.message);
            } else {
                Toast.warning(data.message);
            }
        } catch (err) {
            Toast.error('验证失败');
        }
    },

    async logout() {
        Modal.confirm('退出登录', '确定要退出登录吗？退出后需要重新扫码登录。', async () => {
            try {
                await API.auth.logout();
                Toast.success('已退出登录');
                await LoginPage.refreshStatus();
            } catch (err) {
                Toast.error('退出失败');
            }
        });
    },
};
