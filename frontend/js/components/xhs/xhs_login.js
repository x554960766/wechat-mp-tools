/**
 * 小红书登录管理组件
 */
const XhsLoginPage = {
    _pollTimer: null,

    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">小红书登录管理</h2>
                    <p class="page-description">管理小红书的登录状态。扫码登录后支持获取高清无水印的视频和图文资源。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 20px; padding: 24px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 16px; color: var(--text-primary);">当前登录状态</h3>
                <div id="xhs-login-status-card" style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
                    <div class="spinner" style="width: 20px; height: 20px;"></div>
                    <span style="color: var(--text-secondary);">正在获取登录状态...</span>
                </div>
                
                <div style="display: flex; gap: 12px;">
                    <button class="btn btn-primary" id="btn-xhs-login" onclick="XhsLoginPage.startLogin()">🔑 扫码自动登录</button>
                    <button class="btn btn-danger" id="btn-xhs-logout" style="display: none;" onclick="XhsLoginPage.logout()">退出登录</button>
                </div>
            </div>

            <div id="xhs-scan-status-container" style="margin-bottom: 20px;"></div>

            <div class="card" style="margin-bottom: 20px; padding: 24px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 8px; color: var(--text-primary);">手动配置 Cookie</h3>
                <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 16px;">如果扫码登录失败，您可以手动抓包并粘贴小红书 Cookie 字符串进行配置。</p>
                <div style="margin-bottom: 16px;">
                    <textarea id="xhs-cookie-textarea" class="form-control" rows="4" placeholder="格式如：webId=xxx; web_session=xxx; ..." style="width: 100%; font-family: monospace; font-size: 0.85rem; padding: 12px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary); resize: vertical;"></textarea>
                </div>
                <button class="btn btn-secondary" onclick="XhsLoginPage.saveCookie()">保存 Cookie</button>
            </div>

            <div class="card" style="padding: 24px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 12px; color: var(--text-primary);">如何获取小红书 Cookie？</h3>
                <ol style="color: var(--text-secondary); padding-left: 20px; line-height: 1.8; font-size: 0.9rem;">
                    <li>在电脑浏览器（如 Chrome）中打开并登录 <a href="https://www.xiaohongshu.com" target="_blank" style="color: var(--primary); font-weight: 500;">小红书网页版</a>。</li>
                    <li>按键盘上的 <strong>F12</strong> 或右键选择“检查”打开开发者工具。</li>
                    <li>切换到 <strong>Network (网络)</strong> 选项卡。</li>
                    <li>刷新网页，并在网络请求列表中点击任意一个对 <code>xiaohongshu.com</code> 的请求。</li>
                    <li>在右侧的 <strong>Headers (标头)</strong> -> <strong>Request Headers (请求标头)</strong> 中找到 <code>cookie:</code> 开头的值，复制整段文本粘贴到上面的输入框保存。</li>
                </ol>
            </div>
        `;
    },

    async init() {
        await this.checkStatus();
    },

    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async checkStatus() {
        const card = document.getElementById('xhs-login-status-card');
        const logoutBtn = document.getElementById('btn-xhs-logout');
        const textArea = document.getElementById('xhs-cookie-textarea');
        if (!card) return;

        try {
            const data = await API.xhs.auth.status();
            const settings = await API.settings.get();
            if (textArea && settings.xhs_cookie) {
                textArea.value = settings.xhs_cookie;
            }

            if (data.logged_in) {
                card.innerHTML = `
                    <span style="width: 10px; height: 10px; border-radius: 50%; background: var(--success); display: inline-block; box-shadow: 0 0 6px var(--success);"></span>
                    <strong style="color: var(--success);">已登录 (Cookie 有效)</strong>
                `;
                if (logoutBtn) logoutBtn.style.display = 'inline-block';
            } else if (data.cookie_set) {
                card.innerHTML = `
                    <span style="width: 10px; height: 10px; border-radius: 50%; background: var(--warning); display: inline-block; box-shadow: 0 0 6px var(--warning);"></span>
                    <strong style="color: var(--warning);">已配置游客 Cookie (未登录，无法获取博主笔记列表，视频可能仅低清)</strong>
                `;
                if (logoutBtn) logoutBtn.style.display = 'inline-block';
            } else {
                card.innerHTML = `
                    <span style="width: 10px; height: 10px; border-radius: 50%; background: var(--text-muted); display: inline-block;"></span>
                    <strong style="color: var(--text-secondary);">未配置登录 Cookie (视频仅能下载低清)</strong>
                `;
                if (logoutBtn) logoutBtn.style.display = 'none';
            }
        } catch (err) {
            card.innerHTML = `<span style="color: var(--error);">获取状态失败: ${err.message}</span>`;
        }
    },

    async startLogin() {
        const btn = document.getElementById('btn-xhs-login');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> 启动中...';
        }

        const container = document.getElementById('xhs-scan-status-container');
        if (container) {
            container.innerHTML = `
                <div style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px; text-align: center;">
                    <div class="spinner" style="margin: 0 auto 12px;"></div>
                    <p style="color: var(--text-primary); font-weight: 600;">正在启动浏览器扫码登录流程...</p>
                    <p style="color: var(--text-muted); font-size: 0.85rem;">请在弹出的浏览器窗口中完成小红书登录扫码</p>
                </div>
            `;
        }

        try {
            await API.xhs.auth.login();
            this.startStatusPolling();
        } catch (err) {
            Toast.error('启动登录失败: ' + err.message);
            this._resetLoginButton();
            if (container) container.innerHTML = '';
        }
    },

    startStatusPolling() {
        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = setInterval(async () => {
            try {
                const data = await API.xhs.auth.status();
                const loginState = data.login_state || {};
                const container = document.getElementById('xhs-scan-status-container');

                if (!container) return;

                if (loginState.status === 'scanning') {
                    container.innerHTML = `
                        <div style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px; text-align: center;">
                            <div class="spinner" style="margin: 0 auto 12px;"></div>
                            <p style="color: var(--text-primary); font-weight: 600;">${loginState.message}</p>
                            <div class="progress-bar" style="width: 60%; margin: 12px auto; background: var(--bg-tertiary); height: 8px; border-radius: 4px; overflow: hidden;">
                                <div class="progress-fill" style="width: ${loginState.progress}%; background: var(--primary); height: 100%;"></div>
                            </div>
                        </div>
                    `;
                } else if (loginState.status === 'success') {
                    container.innerHTML = `
                        <div style="background: rgba(7,193,96,0.05); border: 1px solid rgba(7,193,96,0.2); border-radius: 12px; padding: 20px; text-align: center;">
                            <p style="color: var(--success); font-weight: 600;">✅ ${loginState.message}</p>
                        </div>
                    `;
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetLoginButton();
                    setTimeout(() => {
                        this.checkStatus();
                        container.innerHTML = '';
                        App.checkAuthStatus();
                    }, 2000);
                } else if (loginState.status === 'failed') {
                    container.innerHTML = `
                        <div style="background: rgba(255,59,48,0.05); border: 1px solid rgba(255,59,48,0.2); border-radius: 12px; padding: 20px; text-align: center;">
                            <p style="color: var(--error); font-weight: 600;">❌ 登录失败: ${loginState.message}</p>
                            <button class="btn btn-primary btn-sm" style="margin-top: 12px;" onclick="XhsLoginPage.startLogin()">重新登录</button>
                        </div>
                    `;
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetLoginButton();
                } else if (loginState.status === 'idle') {
                    container.innerHTML = '';
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetLoginButton();
                }
            } catch (err) {
                // ignore
            }
        }, 2000);
    },

    _resetLoginButton() {
        const btn = document.getElementById('btn-xhs-login');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '🔑 扫码自动登录';
        }
    },

    async saveCookie() {
        const text = document.getElementById('xhs-cookie-textarea').value.trim();
        if (!text) {
            Toast.error('请输入 Cookie 字符串');
            return;
        }

        try {
            const res = await API.xhs.auth.saveCookie(text);
            if (res.warning) {
                Toast.warning(res.warning);
            } else {
                Toast.success('Cookie 保存成功');
            }
            await this.checkStatus();
            App.checkAuthStatus();
        } catch (err) {
            Toast.error('保存失败: ' + err.message);
        }
    },

    logout() {
        Modal.confirm('退出登录', '确认要退出小红书登录状态并清除 Cookie 吗？', async () => {
            try {
                await API.xhs.auth.logout();
                Toast.success('已清除 Cookie');
                const text = document.getElementById('xhs-cookie-textarea');
                if (text) text.value = '';
                await this.checkStatus();
                App.checkAuthStatus();
            } catch (err) {
                Toast.error('退出失败: ' + err.message);
            }
        });
    }
};
