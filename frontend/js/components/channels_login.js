/**
 * 微信视频号扫码登录（元宝 Cookie 获取）页面组件
 */
const ChannelsLoginPage = {
    cookiePollTimer: null,
    settings: {},

    render() {
        return `
            <div class="page-header animate-fade-in">
                <h2 class="page-title">扫码登录</h2>
                <p class="page-description">通过微信扫码登录腾讯元宝，自动截获核心 Cookie，开启 100% 本地免封锁、免云端中转的高清微信视频号解析下载功能。</p>
            </div>

            <div class="grid grid-2 animate-fade-in" style="gap: var(--spacing-lg);">
                <!-- 登录引导卡片 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">🔑 扫码登录腾讯元宝</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md); display: flex; flex-direction: column; gap: var(--spacing-md);">
                        <div style="background: rgba(7, 193, 96, 0.05); border: 1px solid rgba(7, 193, 96, 0.15); border-radius: 12px; padding: var(--spacing-md); margin-top: var(--spacing-md);">
                            <h4 style="margin: 0 0 var(--spacing-xs); font-size: 0.95rem; color: var(--primary); font-weight: 600;">💡 微信扫码登录说明</h4>
                            <p style="font-size: 0.85rem; line-height: 1.6; color: var(--text-primary); margin: 0;">
                                微信视频号数据经过高度加密。<strong>腾讯元宝 (Yuanbao)</strong> 是腾讯推出的 AI 助手，网页端支持直接读取未加密的视频号直链。
                                <br><br>
                                点击下方按钮后，本软件将在您的电脑本地拉起一个真实的 Chrome 浏览器。您只需在弹出的窗口中完成微信扫码登录并进入元宝页面，系统便会<strong>自动截获</strong>登录 Cookie 并安全保存在本地，全程 100% 安全私密。
                            </p>
                        </div>

                        <div class="form-group" style="margin-top: var(--spacing-sm);">
                            <label class="form-label" style="font-weight: 600; color: var(--text-primary);">当前元宝联通状态</label>
                            <div style="display: flex; align-items: center; gap: var(--spacing-md); margin-top: var(--spacing-xs);">
                                <span class="badge" id="yb-login-status-badge" style="padding: 8px 16px; border-radius: 12px; font-size: 0.85rem; font-weight: 500;">
                                    检测中...
                                </span>
                                <button class="btn btn-primary" id="btn-yb-capture-cookie" onclick="ChannelsLoginPage.startCookieAcquisition()" style="padding: 10px 20px; font-size: 0.9rem; font-weight: 500; display: flex; align-items: center; gap: 8px; border-radius: 8px;">
                                    🚀 自动扫码登录获取
                                </button>
                            </div>
                        </div>

                        <div class="form-hint" style="margin-top: -8px; line-height: 1.4;">
                            * 提示：如果启动失败，请确保电脑上已安装 Google Chrome 浏览器。
                        </div>
                    </div>
                </div>

                <!-- Cookie 管理卡片 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">⚙️ 腾讯元宝 Cookie 凭证 (hy_token)</h3>
                    </div>
                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md); display: flex; flex-direction: column; gap: var(--spacing-md);">
                        <div class="form-group" style="margin-top: var(--spacing-md);">
                            <label class="form-label" style="color: var(--text-primary); font-weight: 500;">元宝 Web 端 Cookie 字符串</label>
                            <textarea id="yb-cookie-textarea" class="form-input" style="height: 125px; font-family: monospace; font-size: 0.85rem; resize: vertical; border-radius: 8px;" placeholder="点击左侧自动获取，或在此直接粘贴元宝 Cookie 字符串..."></textarea>
                            <div class="form-hint" style="margin-top: 6px;">配置 Cookie 后，网址下载将跳过公共解析服务器，直接由您本地发起安全的微信视频号解密抓取。</div>
                        </div>

                        <div style="display: flex; gap: var(--spacing-sm); justify-content: flex-end; margin-top: var(--spacing-xs);">
                            <button class="btn btn-secondary" onclick="ChannelsLoginPage.clearCookie()" style="padding: 8px 16px; border-radius: 8px;">清空凭证</button>
                            <button class="btn btn-primary" onclick="ChannelsLoginPage.saveCookie()" style="padding: 8px 20px; border-radius: 8px;">💾 保存凭证</button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 同步助手 & 拦截代理设置 (证书注入 JS 方式) -->
            <div class="card animate-fade-in" style="margin-top: var(--spacing-lg); border: 1px solid rgba(7, 193, 96, 0.15); background: linear-gradient(135deg, var(--bg-card) 0%, rgba(7, 193, 96, 0.03) 100%);">
                <div class="card-header" style="display: flex; justify-content: space-between; align-items: center; padding-bottom: var(--spacing-sm); border-bottom: 1px solid rgba(7, 193, 96, 0.1);">
                    <h3 class="card-title" style="margin: 0; color: var(--primary); display: flex; align-items: center; gap: 8px;">
                        🔒 微信极速同步助手 <span class="badge" id="proxy-status-badge" style="font-size: 0.75rem; border-radius: 20px; padding: 2px 8px; font-weight: 600;">检测中...</span>
                    </h3>
                    <div style="font-size: 0.85rem; color: var(--text-muted); display: flex; gap: 12px; align-items: center;">
                        <span id="cert-status-badge" style="display: flex; align-items: center; gap: 4px;">CA 证书: <strong style="color: #ff9900;">检测中</strong></span>
                    </div>
                </div>
                
                <div style="padding: var(--spacing-md); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: var(--spacing-md);">
                    <div style="flex: 1; min-width: 300px;">
                        <p style="font-size: 0.9rem; margin: 0 0 8px 0; color: var(--text-primary); font-weight: 500;">
                            通过系统代理拦截并注入辅助脚本，在微信 PC 客户端内自动完成作者全部作品的一键同步。
                        </p>
                        <div style="font-size: 0.8rem; color: var(--text-muted); line-height: 1.5;">
                            1. 点击“启动助手”并允许添加/信任本地 CA 根证书。<br>
                            2. 启动后，在 PC/Mac 微信中打开任意视频号作者的个人主页。<br>
                            3. 微信页面内会自动浮现【同步当前作者作品到系统】按钮，点击即可极速同步！
                        </div>
                    </div>
                    
                    <div style="display: flex; gap: var(--spacing-sm); flex-wrap: wrap; align-items: center;">
                        <button class="btn btn-secondary" id="btn-install-cert" onclick="ChannelsLoginPage.installCert()" style="font-size: 0.85rem; padding: 10px 16px; display: flex; align-items: center; gap: 6px;">
                            🛡️ 安装/信任证书
                        </button>
                        <a href="/api/channels/proxy/download-cert" class="btn btn-secondary" style="font-size: 0.85rem; padding: 10px 16px; text-decoration: none; display: flex; align-items: center; gap: 6px;" title="下载证书供手动安装">
                            📥 下载证书
                        </a>
                        <button class="btn btn-primary" id="btn-toggle-proxy" onclick="ChannelsLoginPage.toggleProxy()" style="min-width: 140px; font-weight: 600; padding: 10px 20px;">
                            🚀 启动同步助手
                        </button>
                        <button class="btn btn-secondary" onclick="ChannelsLoginPage.clearWechatCache()" id="btn-clear-cache" style="font-size: 0.85rem; padding: 10px 16px; color: var(--error, #e53e3e); border-color: rgba(229, 62, 62, 0.3); display: flex; align-items: center; gap: 6px;">
                            🧹 清除微信缓存 (解决打不开/未初始化)
                        </button>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        await this.loadSettings();
        await this.checkProxyStatus();
    },

    destroy() {
        if (this.cookiePollTimer) {
            clearInterval(this.cookiePollTimer);
            this.cookiePollTimer = null;
        }
    },

    async loadSettings() {
        try {
            const data = await API.settings.get();
            this.settings = data;
            this.updateUI(data);
        } catch (err) {
            Toast.error('加载腾讯元宝配置失败');
        }
    },

    updateUI(data) {
        const textarea = document.getElementById('yb-cookie-textarea');
        if (textarea) {
            textarea.value = data.yuanbao_cookie || '';
        }

        const badge = document.getElementById('yb-login-status-badge');
        if (badge) {
            if (data.yuanbao_cookie && data.yuanbao_cookie.trim()) {
                badge.textContent = 'Cookie 已配置';
                badge.className = 'badge badge-success';
                badge.style.color = 'var(--success)';
                badge.style.background = 'rgba(76, 175, 80, 0.1)';
            } else {
                badge.textContent = '未配置 Cookie';
                badge.className = 'badge';
                badge.style.color = 'var(--error)';
                badge.style.background = 'rgba(229, 62, 62, 0.1)';
            }
        }
    },

    async saveCookie() {
        const textarea = document.getElementById('yb-cookie-textarea');
        const cookieVal = textarea ? textarea.value.trim() : '';

        try {
            const settings = await API.settings.get();
            settings.yuanbao_cookie = cookieVal;
            await API.settings.save(settings);
            Toast.success('腾讯元宝 Cookie 保存成功！');
            this.settings = settings;
            this.updateUI(settings);
        } catch (err) {
            Toast.error('保存失败: ' + err.message);
        }
    },

    async clearCookie() {
        Modal.confirm('清除 Cookie 凭证', '确定要清除已保存的腾讯元宝 Cookie 凭证吗？清除后将无法在本地运行解析，解析请求将转发到云端公共中继节点。', async () => {
            try {
                const settings = await API.settings.get();
                settings.yuanbao_cookie = '';
                await API.settings.save(settings);
                Toast.success('元宝凭证已清空');
                this.settings = settings;
                this.updateUI(settings);
            } catch (err) {
                Toast.error('操作失败');
            }
        });
    },

    async startCookieAcquisition() {
        const btn = document.getElementById('btn-yb-capture-cookie');
        const badge = document.getElementById('yb-login-status-badge');

        if (!btn) return;

        btn.disabled = true;
        btn.innerHTML = '<span class="spinner" style="width: 14px; height: 14px; border-width: 2px; display: inline-block; vertical-align: middle; margin-right: 6px;"></span> 启动浏览器中...';

        try {
            const data = await API.channels.startCookieAcquisition();
            Toast.success(data.message);

            if (badge) {
                badge.textContent = '等待扫码登录...';
                badge.style.color = '#3182ce';
                badge.style.background = 'rgba(49, 130, 206, 0.1)';
            }

            if (this.cookiePollTimer) clearInterval(this.cookiePollTimer);

            this.cookiePollTimer = setInterval(async () => {
                try {
                    const status = await API.channels.cookieAcquisitionStatus();

                    if (status.status === 'running') {
                        btn.innerHTML = '<span class="spinner" style="width: 14px; height: 14px; border-width: 2px; display: inline-block; vertical-align: middle; margin-right: 6px;"></span> 等待微信登录中...';
                    } else if (status.status === 'success') {
                        clearInterval(this.cookiePollTimer);
                        this.cookiePollTimer = null;

                        btn.disabled = false;
                        btn.innerHTML = '🚀 自动扫码登录获取';

                        Toast.success('微信扫码登录腾讯元宝成功，Cookie 已自动获取并保存！');
                        await this.loadSettings();
                    } else if (status.status === 'failed') {
                        clearInterval(this.cookiePollTimer);
                        this.cookiePollTimer = null;

                        btn.disabled = false;
                        btn.innerHTML = '🚀 自动扫码登录获取';

                        Toast.error('获取 Cookie 失败: ' + status.error);
                        await this.loadSettings();
                    }
                } catch (err) {
                    clearInterval(this.cookiePollTimer);
                    this.cookiePollTimer = null;
                    btn.disabled = false;
                    btn.innerHTML = '🚀 自动扫码登录获取';
                }
            }, 2000);

        } catch (err) {
            btn.disabled = false;
            btn.innerHTML = '🚀 自动扫码登录获取';
            Toast.error('唤起浏览器失败: ' + err.message);
        }
    },

    async checkProxyStatus() {
        try {
            const status = await API.channels.getProxyStatus();
            this.renderProxyUI(status);
        } catch (err) {
            console.error('Failed to get proxy status:', err);
        }
    },

    renderProxyUI(status) {
        const proxyBadge = document.getElementById('proxy-status-badge');
        const certBadge = document.getElementById('cert-status-badge');
        const toggleBtn = document.getElementById('btn-toggle-proxy');
        const installBtn = document.getElementById('btn-install-cert');
        
        if (!proxyBadge || !toggleBtn) return;
        
        if (status.proxy_running) {
            proxyBadge.textContent = '运行中';
            proxyBadge.style.background = '#07c160';
            proxyBadge.style.color = 'white';
            toggleBtn.textContent = '🛑 关闭同步助手';
            toggleBtn.className = 'btn btn-secondary';
            toggleBtn.style.borderColor = 'rgba(255,59,48,0.2)';
            toggleBtn.style.color = '#ff3b30';
        } else {
            proxyBadge.textContent = '已关闭';
            proxyBadge.style.background = 'rgba(0,0,0,0.08)';
            proxyBadge.style.color = 'var(--text-secondary)';
            toggleBtn.textContent = '🚀 启动同步助手';
            toggleBtn.className = 'btn btn-primary';
            toggleBtn.style.color = '';
            toggleBtn.style.borderColor = '';
        }
        
        if (certBadge && installBtn) {
            if (status.cert_installed) {
                certBadge.innerHTML = 'CA 证书: <strong style="color: #07c160;">已信任</strong>';
                installBtn.style.display = 'none';
            } else {
                certBadge.innerHTML = 'CA 证书: <strong style="color: #ff9500;">未信任</strong>';
                installBtn.style.display = 'inline-flex';
            }
        }
    },

    async toggleProxy() {
        const toggleBtn = document.getElementById('btn-toggle-proxy');
        if (toggleBtn) toggleBtn.disabled = true;
        
        try {
            const status = await API.channels.getProxyStatus();
            if (status.proxy_running) {
                await API.channels.stopProxy();
                Toast.success('同步助手及系统代理已成功关闭');
            } else {
                Toast.info('正在启动服务，请注意系统弹窗并允许证书安装...');
                await API.channels.startProxy();
                Toast.success('同步助手启动成功！已配置系统代理');
            }
            await this.checkProxyStatus();
        } catch (err) {
            Toast.error('同步助手操作失败: ' + err.message);
        } finally {
            if (toggleBtn) toggleBtn.disabled = false;
        }
    },

    async installCert() {
        const installBtn = document.getElementById('btn-install-cert');
        if (installBtn) installBtn.disabled = true;
        
        try {
            Toast.info('请在系统弹窗中确认信任该本地证书...');
            await API.channels.installCert();
            Toast.success('证书安装/信任成功！');
            await this.checkProxyStatus();
        } catch (err) {
            Toast.error('安装证书失败: ' + err.message);
        } finally {
            if (installBtn) installBtn.disabled = false;
        }
    },

    async clearWechatCache() {
        if (!confirm('此操作将尝试清除微信内置浏览器的网页缓存，以解决视频号 API 无法初始化或页面打不开的问题。\n\n请在点击【确定】之前，确保您已在电脑上【彻底退出并关闭微信】！是否继续？')) {
            return;
        }

        const btn = document.getElementById('btn-clear-cache');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="width: 14px; height: 14px; border-width: 2px; display: inline-block; vertical-align: middle; margin-right: 6px;"></span> 正在清除...';
        }

        try {
            const res = await API.channels.clearCache();
            alert(res.message || '已成功清除微信网页缓存！');
            Toast.success('清除缓存成功！');
        } catch (err) {
            // Error already toasted by API wrapper
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '🧹 清除微信缓存 (解决打不开/未初始化)';
            }
        }
    }
};
