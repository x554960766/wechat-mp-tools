const DyLoginComponent = {
    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">抖音扫码登录</h2>
                <p style="color: var(--text-secondary); margin-top: 8px;">扫码登录以获取完整的抖音下载功能</p>
            </div>

            <div class="card login-card" style="max-width: 520px; margin: 40px auto; text-align: center;">
                <div class="card-body">
                    <div id="dy-login-status" style="margin: 30px 0; font-size: 1.1rem; color: var(--text-primary);">
                        点击下方按钮开始登录流程
                    </div>

                    <div id="dy-login-hint" style="display: none; margin: 20px 0; padding: 16px; background: var(--bg-secondary); border-radius: 8px; color: var(--text-secondary); font-size: 0.95rem; line-height: 1.6;">
                        <p style="margin: 0 0 8px 0;">📱 <strong>登录步骤：</strong></p>
                        <ol style="text-align: left; margin: 0; padding-left: 20px;">
                            <li>在弹出的浏览器窗口中点击"登录"按钮</li>
                            <li>使用抖音 App 扫描二维码</li>
                            <li>在手机上确认登录</li>
                            <li>登录成功后会自动保存 Cookie</li>
                        </ol>
                    </div>

                    <div style="display: flex; gap: 12px; justify-content: center; margin-top: 24px;">
                        <button id="btn-dy-start-login" class="btn btn-primary" style="padding: 12px 32px; font-size: 1.1rem; border-radius: 8px;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 20px; height: 20px; margin-right: 8px; display: inline-block; vertical-align: text-bottom;">
                                <path d="M15 3H19C20.1 3 21 3.9 21 5V19C21 20.1 20.1 21 19 21H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <polyline points="10,17 15,12 10,7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                <line x1="15" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            </svg>
                            开始扫码登录
                        </button>

                        <button id="btn-dy-cancel-login" class="btn btn-secondary" style="padding: 12px 32px; font-size: 1.1rem; border-radius: 8px; display: none;">
                            <svg viewBox="0 0 24 24" fill="none" style="width: 20px; height: 20px; margin-right: 8px; display: inline-block; vertical-align: text-bottom;">
                                <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                                <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            </svg>
                            取消登录
                        </button>
                    </div>
                </div>
            </div>
        `;
    },

    init() {
        this.statusTimer = null;
        this.statusText = document.getElementById('dy-login-status');
        this.loginHint = document.getElementById('dy-login-hint');
        this.startBtn = document.getElementById('btn-dy-start-login');
        this.cancelBtn = document.getElementById('btn-dy-cancel-login');

        if (this.startBtn) {
            this.startBtn.addEventListener('click', () => this.startLogin());
        }

        if (this.cancelBtn) {
            this.cancelBtn.addEventListener('click', () => this.cancelLogin());
        }

        // 初始化时检查一次状态
        this.checkStatus();
    },

    async startLogin() {
        try {
            this.startBtn.disabled = true;
            this.cancelBtn.style.display = 'none';
            this.statusText.textContent = "正在初始化浏览器，请稍候...";
            this.statusText.style.color = "var(--text-primary)";
            this.loginHint.style.display = 'none';

            const res = await API.douyin.auth.start();
            Toast.show(res.message, 'success');

            // 显示取消按钮
            this.cancelBtn.style.display = 'inline-flex';

            // 启动轮询
            if (this.statusTimer) clearInterval(this.statusTimer);
            this.statusTimer = setInterval(() => this.checkStatus(), 2000);
        } catch (err) {
            Toast.show(err.message, 'error');
            this.startBtn.disabled = false;
            this.cancelBtn.style.display = 'none';
            this.statusText.textContent = "启动失败，请重试";
            this.statusText.style.color = "var(--error)";
        }
    },

    async cancelLogin() {
        try {
            this.cancelBtn.disabled = true;
            const res = await API.douyin.auth.cancel();
            Toast.show(res.message, 'info');

            // 停止轮询
            if (this.statusTimer) {
                clearInterval(this.statusTimer);
                this.statusTimer = null;
            }

            // 重置 UI
            this.startBtn.disabled = false;
            this.cancelBtn.style.display = 'none';
            this.cancelBtn.disabled = false;
            this.loginHint.style.display = 'none';
            this.statusText.textContent = "登录已取消";
            this.statusText.style.color = "var(--text-secondary)";
        } catch (err) {
            Toast.show(err.message, 'error');
            this.cancelBtn.disabled = false;
        }
    },

    async checkStatus() {
        try {
            const data = await API.douyin.auth.status();
            const logoutBtn = document.getElementById('btn-dy-logout');

            if (data.status === 'scanning') {
                this.startBtn.disabled = true;
                this.cancelBtn.style.display = 'inline-flex';
                this.cancelBtn.disabled = false;
                this.statusText.textContent = data.message || "请在浏览器窗口中扫码...";
                this.statusText.style.color = "var(--primary)";
                this.loginHint.style.display = 'block';
                if (logoutBtn) logoutBtn.style.display = 'none';
            } else if (data.status === 'success') {
                this.loginHint.style.display = 'none';
                
                const info = data.account_info || {};
                this.statusText.innerHTML = `
                    <div style="text-align: center; margin-bottom: var(--spacing-md);">
                        <p style="color: var(--success); font-weight: 600; font-size: 1.2rem; margin-bottom: 20px;">✅ 抖音登录成功，Cookie 已就绪</p>
                        ${info.nickname ? `
                        <div class="douyin-profile-card animate-fade-in" style="display: flex; align-items: center; gap: 16px; max-width: 380px; margin: 0 auto; padding: 18px; background: rgba(29, 29, 31, 0.03); border-radius: 16px; border: 1px solid var(--border-color); text-align: left; box-shadow: var(--shadow-sm);">
                            ${info.avatar ? `
                                <img src="${info.avatar}" alt="Avatar" style="width: 64px; height: 64px; border-radius: 50%; border: 2px solid white; box-shadow: var(--shadow-sm); object-fit: cover;" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                                <div style="display: none; width: 64px; height: 64px; border-radius: 50%; background: #fe2c55; color: white; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; border: 2px solid white; box-shadow: var(--shadow-sm);">${info.nickname.charAt(0)}</div>
                            ` : `
                                <div style="width: 64px; height: 64px; border-radius: 50%; background: #fe2c55; color: white; display: flex; align-items: center; justify-content: center; font-size: 1.8rem; font-weight: 800; border: 2px solid white; box-shadow: var(--shadow-sm);">${info.nickname.charAt(0)}</div>
                            `}
                            <div style="flex: 1; overflow: hidden;">
                                <h4 style="margin: 0; font-size: 1.2rem; font-weight: 800; color: var(--text-primary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${info.nickname}</h4>
                                <p style="margin: 4px 0 0 0; font-size: 0.82rem; color: var(--text-muted);">抖音号: ${info.unique_id || '未设定'}</p>
                                ${info.signature ? `<p style="margin: 4px 0 0 0; font-size: 0.78rem; color: var(--text-secondary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap;" title="${info.signature}">${info.signature}</p>` : ''}
                            </div>
                        </div>
                        ` : `
                        <p style="color: var(--text-secondary);">登录凭证有效，但未能获取个人资料</p>
                        `}
                    </div>
                `;
                this.statusText.style.color = "var(--text-primary)";
                this.startBtn.disabled = false;
                this.startBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" style="width: 20px; height: 20px; margin-right: 8px; display: inline-block; vertical-align: text-bottom;">
                        <path d="M15 3H19C20.1 3 21 3.9 21 5V19C21 20.1 20.1 21 19 21H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="10,17 15,12 10,7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <line x1="15" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    重新登录
                `;
                this.cancelBtn.style.display = 'none';

                // 显示一键退出登录按钮
                if (!logoutBtn) {
                    const newLogoutBtn = document.createElement('button');
                    newLogoutBtn.id = 'btn-dy-logout';
                    newLogoutBtn.className = 'btn btn-danger';
                    newLogoutBtn.style.padding = '12px 32px';
                    newLogoutBtn.style.fontSize = '1.1rem';
                    newLogoutBtn.style.borderRadius = '8px';
                    newLogoutBtn.innerHTML = `
                        <svg viewBox="0 0 24 24" fill="none" style="width: 20px; height: 20px; margin-right: 8px; display: inline-block; vertical-align: text-bottom;">
                            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <polyline points="16 17 21 12 16 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <line x1="21" y1="12" x2="9" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        退出登录
                    `;
                    newLogoutBtn.addEventListener('click', () => this.logout());
                    this.startBtn.parentNode.appendChild(newLogoutBtn);
                } else {
                    logoutBtn.style.display = 'inline-flex';
                }

                if (this.statusTimer) {
                    clearInterval(this.statusTimer);
                    this.statusTimer = null;
                }

                // 刷新全局状态
                if (window.App && typeof App.checkAuthStatus === 'function') {
                    App.checkAuthStatus();
                }
            } else if (data.status === 'error' || data.status === 'expired') {
                this.loginHint.style.display = 'none';
                this.statusText.textContent = `❌ ${data.message}`;
                this.statusText.style.color = "var(--error)";
                this.startBtn.disabled = false;
                this.cancelBtn.style.display = 'none';
                if (logoutBtn) logoutBtn.style.display = 'none';

                if (this.statusTimer) {
                    clearInterval(this.statusTimer);
                    this.statusTimer = null;
                }
            } else if (data.status === 'cancelled') {
                this.loginHint.style.display = 'none';
                this.statusText.textContent = "登录已取消";
                this.statusText.style.color = "var(--text-secondary)";
                this.startBtn.disabled = false;
                this.cancelBtn.style.display = 'none';
                if (logoutBtn) logoutBtn.style.display = 'none';

                if (this.statusTimer) {
                    clearInterval(this.statusTimer);
                    this.statusTimer = null;
                }
            } else {
                // idle
                this.loginHint.style.display = 'none';
                this.startBtn.disabled = false;
                this.cancelBtn.style.display = 'none';
                if (logoutBtn) logoutBtn.style.display = 'none';
            }
        } catch (err) {
            console.error('Check dy auth status error:', err);
        }
    },

    async logout() {
        Modal.confirm('退出抖音登录', '确定要退出抖音登录吗？退出后需要重新扫码获取 Cookie。', async () => {
            try {
                const logoutBtn = document.getElementById('btn-dy-logout');
                if (logoutBtn) logoutBtn.disabled = true;
                
                const res = await fetch('/api/douyin-auth/logout', { method: 'POST' });
                const data = await res.json();
                
                Toast.show(data.message, 'success');
                
                // 重置 UI 到 idle
                this.statusText.innerHTML = "点击下方按钮开始登录流程";
                this.statusText.style.color = "var(--text-secondary)";
                this.startBtn.disabled = false;
                this.startBtn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" style="width: 20px; height: 20px; margin-right: 8px; display: inline-block; vertical-align: text-bottom;">
                        <path d="M15 3H19C20.1 3 21 3.9 21 5V19C21 20.1 20.1 21 19 21H15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="10,17 15,12 10,7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <line x1="15" y1="12" x2="3" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    开始扫码登录
                `;
                if (logoutBtn) {
                    logoutBtn.style.display = 'none';
                    logoutBtn.disabled = false;
                }
                
                // 刷新全局状态
                if (window.App && typeof App.checkAuthStatus === 'function') {
                    await App.checkAuthStatus();
                }
            } catch (err) {
                Toast.show(err.message, 'error');
                const logoutBtn = document.getElementById('btn-dy-logout');
                if (logoutBtn) logoutBtn.disabled = false;
            }
        });
    },

    destroy() {
        if (this.statusTimer) {
            clearInterval(this.statusTimer);
            this.statusTimer = null;
        }
    }
};
