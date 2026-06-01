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

            if (data.status === 'scanning') {
                this.startBtn.disabled = true;
                this.cancelBtn.style.display = 'inline-flex';
                this.cancelBtn.disabled = false;
                this.statusText.textContent = data.message || "请在浏览器窗口中扫码...";
                this.statusText.style.color = "var(--primary)";
                this.loginHint.style.display = 'block';
            } else if (data.status === 'success') {
                this.loginHint.style.display = 'none';
                this.statusText.textContent = "✅ 登录成功！Cookie 已保存";
                this.statusText.style.color = "var(--success)";
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

                if (this.statusTimer) {
                    clearInterval(this.statusTimer);
                    this.statusTimer = null;
                }

                // 刷新全局状态
                if (window.App && typeof App.checkAuthStatus === 'function') {
                    App.checkAuthStatus();
                }

                Toast.show('登录成功！', 'success');
            } else if (data.status === 'error' || data.status === 'expired') {
                this.loginHint.style.display = 'none';
                this.statusText.textContent = `❌ ${data.message}`;
                this.statusText.style.color = "var(--error)";
                this.startBtn.disabled = false;
                this.cancelBtn.style.display = 'none';

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

                if (this.statusTimer) {
                    clearInterval(this.statusTimer);
                    this.statusTimer = null;
                }
            } else {
                // idle
                this.loginHint.style.display = 'none';
                this.startBtn.disabled = false;
                this.cancelBtn.style.display = 'none';
            }
        } catch (err) {
            console.error('Check dy auth status error:', err);
        }
    },

    destroy() {
        if (this.statusTimer) {
            clearInterval(this.statusTimer);
            this.statusTimer = null;
        }
    }
};
