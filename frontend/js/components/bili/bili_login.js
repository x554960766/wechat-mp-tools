/**
 * B站登录管理组件
 */
const BiliLoginPage = {
    _pollTimer: null,
    _qrcodeKey: null,

    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">B站登录管理</h2>
                    <p class="page-description">管理哔哩哔哩（Bilibili）的登录状态。扫码登录后支持获取 1080P/4K 高清视频、大会员专属内容、弹幕和字幕。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 20px; padding: 24px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 16px; color: var(--text-primary);">当前登录状态</h3>
                <div id="bili-login-status-card" style="display: flex; align-items: center; gap: 16px; margin-bottom: 20px;">
                    <div class="spinner" style="width: 20px; height: 20px;"></div>
                    <span style="color: var(--text-secondary);">正在获取登录状态...</span>
                </div>
                
                <div style="display: flex; gap: 12px;">
                    <button class="btn btn-primary" id="btn-bili-login-qr" onclick="BiliLoginPage.startQrLogin()">🔑 扫码登录 B站</button>
                    <button class="btn btn-danger" id="btn-bili-logout" style="display: none;" onclick="BiliLoginPage.logout()">退出登录</button>
                </div>
            </div>

            <div id="bili-scan-status-container" style="margin-bottom: 20px; display: none;">
                <div class="card animate-fade-in" style="padding: 24px; text-align: center; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; max-width: 400px; margin: 0 auto;">
                    <h3 style="margin-bottom: 12px; color: var(--text-primary);">微信/手机B站扫码</h3>
                    <div id="bili-qr-box" style="margin: 16px auto; width: 200px; height: 200px; border: 1px solid var(--border-color); display: flex; align-items: center; justify-content: center; position: relative; background: white; border-radius: 8px; padding: 8px;">
                        <div class="spinner"></div>
                    </div>
                    <p id="bili-qr-text" style="color: var(--text-primary); font-weight: 600; margin-bottom: 16px;">正在生成登录二维码...</p>
                    <button class="btn btn-secondary btn-sm" onclick="BiliLoginPage.cancelQrLogin()">取消</button>
                </div>
            </div>

            <div class="card" style="margin-bottom: 20px; padding: 24px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 8px; color: var(--text-primary);">手动配置 Cookie</h3>
                <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 16px;">如果扫码登录失败，您可以手动粘贴 Bilibili Cookie 字符串进行配置。</p>
                <div style="margin-bottom: 16px;">
                    <textarea id="bili-cookie-textarea" class="form-control" rows="4" placeholder="格式如：SESSDATA=xxx; bili_jct=xxx; DedeUserID=xxx; ..." style="width: 100%; font-family: monospace; font-size: 0.85rem; padding: 12px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary); resize: vertical;"></textarea>
                </div>
                <button class="btn btn-secondary" onclick="BiliLoginPage.saveCookie()">保存 Cookie</button>
            </div>

            <div class="card" style="padding: 24px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 12px; color: var(--text-primary);">如何获取 Bilibili Cookie？</h3>
                <ol style="color: var(--text-secondary); padding-left: 20px; line-height: 1.8; font-size: 0.9rem;">
                    <li>在电脑浏览器（如 Chrome）中打开并登录 <a href="https://www.bilibili.com" target="_blank" style="color: var(--primary); font-weight: 500;">Bilibili 网页版</a>。</li>
                    <li>按键盘上的 <strong>F12</strong> 或右键选择“检查”打开开发者工具。</li>
                    <li>切换到 <strong>Network (网络)</strong> 选项卡。</li>
                    <li>刷新网页，并在网络请求列表中点击任意一个对 <code>bilibili.com</code> 的请求。</li>
                    <li>在右侧的 <strong>Headers (标头)</strong> -> <strong>Request Headers (请求标头)</strong> 中找到 <code>cookie:</code> 开头的值，复制整段文本粘贴到上面的输入框保存。</li>
                </ol>
            </div>
        `;
    },

    async init() {
        await this.checkStatus();
    },

    destroy() {
        this.clearTimer();
    },

    clearTimer() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async checkStatus() {
        const card = document.getElementById('bili-login-status-card');
        const logoutBtn = document.getElementById('btn-bili-logout');
        const qreBtn = document.getElementById('btn-bili-login-qr');
        const textArea = document.getElementById('bili-cookie-textarea');
        if (!card) return;

        try {
            const data = await API.bili.auth.status();
            const settings = await API.settings.get();
            if (textArea && settings.bilibili_cookie) {
                textArea.value = settings.bilibili_cookie;
            }

            if (data.logged_in) {
                const info = data.account_info || {};
                card.innerHTML = `
                    ${info.avatar ? `
                        <img src="${info.avatar}" alt="Avatar" style="width: 48px; height: 48px; border-radius: 50%; object-fit: cover; border: 1px solid var(--border-color);" referrerpolicy="no-referrer" />
                    ` : `
                        <div style="width: 48px; height: 48px; border-radius: 50%; background: var(--primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 1.2rem; font-weight: bold;">B</div>
                    `}
                    <div>
                        <strong style="color: var(--success); font-size: 1.1rem; display: block;">已登录 B站 (Cookie 有效)</strong>
                        <span style="color: var(--text-primary); font-weight: 500; font-size: 0.95rem;">
                            昵称: ${info.nickname || '未知用户'} 
                            ${info.vip_status ? '<span class="badge badge-primary" style="background: #fb7299; color: white; border: none; font-size: 0.75rem; padding: 2px 6px; margin-left: 6px;">大会员</span>' : ''}
                        </span>
                        <span style="color: var(--text-muted); font-size: 0.8rem; display: block; margin-top: 2px;">UID: ${info.mid || ''}</span>
                    </div>
                `;
                if (logoutBtn) logoutBtn.style.display = 'inline-block';
                if (qreBtn) qreBtn.style.display = 'none';
            } else {
                card.innerHTML = `
                    <span style="width: 12px; height: 12px; border-radius: 50%; background: var(--text-muted); display: inline-block;"></span>
                    <div>
                        <strong style="color: var(--text-secondary);">未配置登录 Cookie (无法下载 1080P 高码率、4K、大会员视频)</strong>
                    </div>
                `;
                if (logoutBtn) logoutBtn.style.display = 'none';
                if (qreBtn) qreBtn.style.display = 'inline-block';
            }
        } catch (err) {
            card.innerHTML = `<span style="color: var(--error);">获取状态失败: ${err.message}</span>`;
        }
    },

    async startQrLogin() {
        const container = document.getElementById('bili-scan-status-container');
        const qrBox = document.getElementById('bili-qr-box');
        const qrText = document.getElementById('bili-qr-text');
        
        if (container) container.style.display = 'block';
        if (qrBox) qrBox.innerHTML = '<div class="spinner"></div>';
        if (qrText) qrText.textContent = '正在请求授权二维码...';

        try {
            const data = await API.bili.auth.generateQrcode();
            if (!data.url || !data.qrcode_key) {
                throw new Error('接口返回二维码参数缺失');
            }
            
            this._qrcodeKey = data.qrcode_key;
            
            // Render QR code image using free qrserver API
            const qrImageUrl = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(data.url)}`;
            if (qrBox) {
                qrBox.innerHTML = `<img src="${qrImageUrl}" alt="Bilibili QR Code" style="width: 180px; height: 180px;" />`;
            }
            if (qrText) qrText.textContent = '请使用哔哩哔哩 App 扫码并确认登录';

            this.startPolling();
        } catch (err) {
            Toast.error('生成二维码失败: ' + err.message);
            if (container) container.style.display = 'none';
        }
    },

    startPolling() {
        this.clearTimer();
        this._pollTimer = setInterval(async () => {
            if (!this._qrcodeKey) return;
            try {
                const res = await API.bili.auth.pollQrcode(this._qrcodeKey);
                const qrText = document.getElementById('bili-qr-text');
                
                if (res.status === 'not_scanned') {
                    if (qrText) qrText.textContent = '请使用哔哩哔哩 App 扫码登录';
                } else if (res.status === 'scanned') {
                    if (qrText) qrText.textContent = '已扫码，请在手机上确认同意登录';
                } else if (res.status === 'expired') {
                    if (qrText) qrText.innerHTML = '<span style="color: var(--error);">二维码已失效，请重新生成</span>';
                    this.clearTimer();
                } else if (res.status === 'success') {
                    if (qrText) qrText.innerHTML = '<span style="color: var(--success); font-weight: bold;">✅ 登录成功！正在保存状态...</span>';
                    this.clearTimer();
                    Toast.success('登录成功');
                    setTimeout(async () => {
                        const container = document.getElementById('bili-scan-status-container');
                        if (container) container.style.display = 'none';
                        await this.checkStatus();
                        if (window.App && typeof App.checkAuthStatus === 'function') {
                            App.checkAuthStatus();
                        }
                    }, 2000);
                } else if (res.status === 'failed') {
                    if (qrText) qrText.innerHTML = `<span style="color: var(--error);">登录失败: ${res.message || '未知错误'}</span>`;
                    this.clearTimer();
                }
            } catch (err) {
                // Keep polling
            }
        }, 1500);
    },

    cancelQrLogin() {
        this.clearTimer();
        const container = document.getElementById('bili-scan-status-container');
        if (container) container.style.display = 'none';
    },

    async saveCookie() {
        const text = document.getElementById('bili-cookie-textarea').value.trim();
        if (!text) {
            Toast.error('请输入 Cookie 字符串');
            return;
        }

        try {
            const res = await API.bili.auth.saveCookie(text);
            Toast.success('Cookie 保存成功');
            await this.checkStatus();
            if (window.App && typeof App.checkAuthStatus === 'function') {
                App.checkAuthStatus();
            }
        } catch (err) {
            Toast.error('保存失败: ' + err.message);
        }
    },

    logout() {
        Modal.confirm('退出登录', '确认要退出哔哩哔哩登录状态并清除 Cookie 吗？', async () => {
            try {
                await API.bili.auth.logout();
                Toast.success('已退出登录并清除 Cookie');
                const text = document.getElementById('bili-cookie-textarea');
                if (text) text.value = '';
                await this.checkStatus();
                if (window.App && typeof App.checkAuthStatus === 'function') {
                    App.checkAuthStatus();
                }
            } catch (err) {
                Toast.error('退出失败: ' + err.message);
            }
        });
    }
};
