const KsLoginComponent = {
    settings: {},

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">快手扫码登录</h2>
                <p style="color: var(--text-secondary); margin-top: 8px;">扫码登录或手动配置 Cookie。单作品下载可匿名使用；用户主页批量下载需要登录。</p>
            </div>

            <div class="card login-card" style="max-width: 520px; margin: 40px auto; text-align: center;">
                <div class="card-body">
                    <div id="ks-login-status" style="margin: 30px 0; font-size: 1.1rem; color: var(--text-primary);">
                        点击下方按钮开始登录流程
                    </div>

                    <div id="ks-login-hint" style="display: none; margin: 20px 0; padding: 16px; background: var(--bg-secondary); border-radius: 8px; color: var(--text-secondary); font-size: 0.95rem; line-height: 1.6;">
                        <p style="margin: 0 0 8px 0;">📱 <strong>登录步骤：</strong></p>
                        <ol style="text-align: left; margin: 0; padding-left: 20px;">
                            <li>在弹出的浏览器窗口中点击"登录"按钮</li>
                            <li>使用快手 App 扫描二维码</li>
                            <li>在手机上确认登录</li>
                            <li>登录成功后会自动保存 Cookie</li>
                        </ol>
                    </div>

                    <div style="display: flex; gap: 12px; justify-content: center; margin-top: 24px;">
                        <button id="btn-ks-start-login" class="btn btn-primary" style="padding: 12px 32px; font-size: 1.1rem; border-radius: 8px;">
                            开始扫码登录
                        </button>
                        <button id="btn-ks-cancel-login" class="btn btn-secondary" style="padding: 12px 32px; font-size: 1.1rem; border-radius: 8px; display: none;">
                            取消登录
                        </button>
                    </div>
                </div>
            </div>

            <!-- 手动配置 Cookie 卡片 -->
            <div class="card" style="max-width: 520px; margin: 24px auto;">
                <div class="card-header">
                    <h3 class="card-title">⚙️ 手动填入 Cookie (高级)</h3>
                </div>
                <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                    <div class="form-group" style="margin-top: var(--spacing-md); text-align: left;">
                        <label class="form-label" style="color: var(--text-primary); font-weight: 500;">快手网页版 Cookie 字符串</label>
                        <textarea id="ks-cookie-textarea" class="form-input" style="height: 100px; font-family: monospace; font-size: 0.85rem; resize: vertical; border-radius: 8px;" placeholder="点击上面按钮自动获取，或者在此手动粘贴您的快手 Cookie..."></textarea>
                        <div class="form-hint" style="margin-top: 6px;">Cookie 优先级最高。手动粘贴并保存 Cookie 后将立即生效。</div>
                    </div>

                    <div style="display: flex; gap: var(--spacing-sm); justify-content: flex-end; margin-top: var(--spacing-md);">
                        <button class="btn btn-secondary" onclick="KsLoginComponent.clearCookie()" style="padding: 8px 16px; border-radius: 8px;">清空</button>
                        <button class="btn btn-primary" onclick="KsLoginComponent.saveCookie()" style="padding: 8px 20px; border-radius: 8px;">💾 保存凭证</button>
                    </div>
                </div>
            </div>
        `;
    },

    init() {
        this.statusTimer = null;
        this.statusText = document.getElementById('ks-login-status');
        this.loginHint = document.getElementById('ks-login-hint');
        this.startBtn = document.getElementById('btn-ks-start-login');
        this.cancelBtn = document.getElementById('btn-ks-cancel-login');

        if (this.startBtn) this.startBtn.addEventListener('click', () => this.startLogin());
        if (this.cancelBtn) this.cancelBtn.addEventListener('click', () => this.cancelLogin());

        this.checkStatus();
        this.loadSettings();
    },

    async loadSettings() {
        try {
            const data = await API.settings.get();
            this.settings = data;
            const textarea = document.getElementById('ks-cookie-textarea');
            if (textarea) textarea.value = data.kuaishou_cookie || '';
        } catch (err) {
            console.error('Failed to load settings:', err);
        }
    },

    async saveCookie() {
        const textarea = document.getElementById('ks-cookie-textarea');
        const cookieVal = textarea ? textarea.value.trim() : '';
        try {
            const settings = await API.settings.get();
            settings.kuaishou_cookie = cookieVal;
            await API.settings.save(settings);
            Toast.show('快手 Cookie 保存成功！', 'success');
            this.settings = settings;
            this.checkStatus();
        } catch (err) {
            Toast.show('保存失败: ' + err.message, 'error');
        }
    },

    async clearCookie() {
        Modal.confirm('清除 Cookie 凭证', '确定要清除已保存的快手 Cookie 凭证吗？', async () => {
            try {
                const settings = await API.settings.get();
                settings.kuaishou_cookie = '';
                await API.settings.save(settings);
                Toast.show('快手 Cookie 凭证已清空', 'success');
                this.settings = settings;
                const textarea = document.getElementById('ks-cookie-textarea');
                if (textarea) textarea.value = '';
                this.checkStatus();
            } catch (err) {
                Toast.show('操作失败: ' + err.message, 'error');
            }
        });
    },

    async startLogin() {
        try {
            this.startBtn.disabled = true;
            this.cancelBtn.style.display = 'none';
            this.statusText.textContent = "正在初始化浏览器，请稍候...";
            this.statusText.style.color = "var(--text-primary)";
            this.loginHint.style.display = 'none';

            const res = await API.kuaishou.auth.start();
            Toast.show(res.message, 'success');

            this.cancelBtn.style.display = 'inline-flex';

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
            const res = await API.kuaishou.auth.cancel();
            Toast.show(res.message, 'info');

            if (this.statusTimer) {
                clearInterval(this.statusTimer);
                this.statusTimer = null;
            }

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
            const data = await API.kuaishou.auth.status();

            if (data.status === 'scanning') {
                this.startBtn.disabled = true;
                this.cancelBtn.style.display = 'inline-flex';
                this.cancelBtn.disabled = false;
                this.statusText.textContent = data.message || "请在浏览器窗口中扫码...";
                this.statusText.style.color = "var(--primary)";
                this.loginHint.style.display = 'block';
            } else if (data.status === 'success') {
                this.loginHint.style.display = 'none';
                this.statusText.innerHTML = `<p style="color: var(--success); font-weight: 600; font-size: 1.2rem;">✅ 快手登录成功，Cookie 已就绪</p>`;
                this.statusText.style.color = "var(--text-primary)";
                this.startBtn.disabled = false;
                this.startBtn.textContent = "重新登录";
                this.cancelBtn.style.display = 'none';

                // 同步 Cookie 到手动输入框
                if (data.cookie) {
                    const textarea = document.getElementById('ks-cookie-textarea');
                    if (textarea && !textarea.value) textarea.value = data.cookie;
                }

                if (this.statusTimer) {
                    clearInterval(this.statusTimer);
                    this.statusTimer = null;
                }
            } else if (data.status === 'expired') {
                this.loginHint.style.display = 'none';
                this.statusText.innerHTML = `<div style="background: rgba(255,152,0,0.12); border: 1px solid rgba(255,152,0,0.4); border-radius: 8px; padding: 16px; text-align: center;">
                    <p style="color: #ff9800; font-weight: 600; font-size: 1.15rem; margin: 0 0 6px 0;">⚠️ 登录已失效</p>
                    <p style="color: var(--text-secondary); margin: 0; font-size: 0.95rem;">${data.message || '快手 Cookie 已过期，请点击下方按钮重新扫码登录'}</p>
                </div>`;
                this.startBtn.disabled = false;
                this.startBtn.textContent = "重新扫码登录";
                this.cancelBtn.style.display = 'none';

                if (this.statusTimer) {
                    clearInterval(this.statusTimer);
                    this.statusTimer = null;
                }
            } else if (data.status === 'error') {
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
                this.loginHint.style.display = 'none';
                this.startBtn.disabled = false;
                this.cancelBtn.style.display = 'none';
            }
        } catch (err) {
            console.error('Check ks auth status error:', err);
        }
    },

    destroy() {
        if (this.statusTimer) {
            clearInterval(this.statusTimer);
            this.statusTimer = null;
        }
    }
};
