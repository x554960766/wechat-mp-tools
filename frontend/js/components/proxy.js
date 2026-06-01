/**
 * 代理设置页面组件
 */
const ProxyPage = {
    config: {},
    pool: [],

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">代理设置</h2>
                <p class="page-description">配置单代理或代理池，防止请求过于频繁被微信暂时封禁 IP</p>
            </div>

            <div class="grid grid-2" style="gap: var(--spacing-lg); align-items: start;">
                <!-- 单代理配置 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">🌐 单代理配置</h3>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span class="badge" id="proxy-status-badge">已关闭</span>
                            <label class="switch" style="position: relative; display: inline-block; width: 44px; height: 22px;">
                                <input type="checkbox" id="proxy-enabled" onchange="ProxyPage.toggleProxy(this.checked)" style="opacity: 0; width: 0; height: 0;">
                                <span class="slider" style="position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: var(--border); transition: .3s; border-radius: 22px;"></span>
                            </label>
                        </div>
                    </div>

                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);" id="single-proxy-fields">
                        <div class="grid grid-2" style="gap: 12px; margin-bottom: 12px;">
                            <div class="form-group">
                                <label class="form-label" for="proxy-type">代理类型</label>
                                <select class="form-input" id="proxy-type">
                                    <option value="http">HTTP</option>
                                    <option value="socks5">SOCKS5</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label class="form-label" for="proxy-port">端口</label>
                                <input type="number" class="form-input" id="proxy-port" placeholder="例如: 8080" />
                            </div>
                        </div>

                        <div class="form-group">
                            <label class="form-label" for="proxy-host">服务器主机 (IP / 域名)</label>
                            <input type="text" class="form-input" id="proxy-host" placeholder="例如: 127.0.0.1 或 proxy.com" />
                        </div>

                        <div class="grid grid-2" style="gap: 12px; margin-bottom: 12px;">
                            <div class="form-group">
                                <label class="form-label" for="proxy-username">用户名 (可选)</label>
                                <input type="text" class="form-input" id="proxy-username" placeholder="留空则无" />
                            </div>
                            <div class="form-group">
                                <label class="form-label" for="proxy-password">密码 (可选)</label>
                                <input type="password" class="form-input" id="proxy-password" placeholder="留空则无" />
                            </div>
                        </div>

                        <div style="margin-top: var(--spacing-md); display: flex; gap: 8px;">
                            <button class="btn btn-secondary" onclick="ProxyPage.testSingleProxy()" id="btn-test-proxy" style="flex: 1;">
                                ⚡ 测试连接
                            </button>
                            <button class="btn btn-primary" onclick="ProxyPage.saveSingleProxy()" style="flex: 1;">
                                💾 保存配置
                            </button>
                        </div>
                    </div>
                </div>

                <!-- 代理池管理 -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">📚 多代理池管理</h3>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span style="font-size: 0.82rem; color: var(--text-muted);">启用轮换</span>
                            <label class="switch" style="position: relative; display: inline-block; width: 44px; height: 22px;">
                                <input type="checkbox" id="proxy-rotation" onchange="ProxyPage.toggleRotation(this.checked)" style="opacity: 0; width: 0; height: 0;">
                                <span class="slider" style="position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: var(--border); transition: .3s; border-radius: 22px;"></span>
                            </label>
                        </div>
                    </div>

                    <div class="card-body" style="padding: 0 var(--spacing-md) var(--spacing-md);">
                        <!-- 添加代理 IP -->
                        <div style="padding: var(--spacing-sm); border: 1px dashed var(--border); border-radius: var(--border-radius-md); margin-bottom: var(--spacing-md);">
                            <h4 style="margin: 0 0 var(--spacing-xs) 0; font-size: 0.88rem; color: var(--text-primary);">➕ 快速添加代理 IP</h4>
                            <div style="display: flex; gap: 8px;">
                                <select class="form-input" id="pool-type" style="width: 90px; padding: 4px var(--spacing-xs); height: 36px;">
                                    <option value="http">HTTP</option>
                                    <option value="socks5">SOCKS5</option>
                                </select>
                                <input type="text" class="form-input" id="pool-url" placeholder="格式: [用户名:密码@]主机:端口" style="flex: 1; height: 36px; font-size: 0.82rem;" />
                                <button class="btn btn-primary btn-sm" onclick="ProxyPage.addToPool()" style="height: 36px;">添加</button>
                            </div>
                            <div class="form-hint" style="font-size: 0.72rem; margin-top: 4px;">支持输入 host:port 或 user:pass@host:port</div>
                        </div>

                        <!-- 代理池列表 -->
                        <h4 style="margin: 0 0 var(--spacing-xs) 0; font-size: 0.88rem; color: var(--text-primary);">代理池列表 (<span id="pool-count">0</span>)</h4>
                        <div id="proxy-pool-list" style="max-height: 250px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--border-radius-md); background: rgba(0,0,0,0.2);">
                            <div style="text-align: center; color: var(--text-muted); padding: 16px; font-size: 0.82rem;">暂无可用代理 IP</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        // 添加 switch 控件样式 (动态注入，免得修改 style.css)
        this.ensureSwitchStyles();
        await this.loadConfig();
        await this.loadPool();
    },

    destroy() {},

    ensureSwitchStyles() {
        if (document.getElementById('switch-custom-css')) return;
        const style = document.createElement('style');
        style.id = 'switch-custom-css';
        style.textContent = `
            .switch input:checked + .slider {
                background-color: var(--primary) !important;
            }
            .switch .slider:before {
                position: absolute;
                content: "";
                height: 16px;
                width: 16px;
                left: 3px;
                bottom: 3px;
                background-color: #fff;
                transition: .3s;
                border-radius: 50%;
            }
            .switch input:checked + .slider:before {
                transform: translateX(22px);
            }
        `;
        document.head.appendChild(style);
    },

    async loadConfig() {
        try {
            const data = await API.proxy.getConfig();
            this.config = data;
            this.populateConfigUI(data);
        } catch (err) {
            Toast.error('加载代理配置失败');
        }
    },

    async loadPool() {
        try {
            const data = await API.proxy.getPool();
            this.pool = data.pool || [];
            this.populatePoolUI();
        } catch (err) {
            // error handled
        }
    },

    populateConfigUI(data) {
        const enabledCheck = document.getElementById('proxy-enabled');
        const rotationCheck = document.getElementById('proxy-rotation');
        const typeSelect = document.getElementById('proxy-type');
        const hostInput = document.getElementById('proxy-host');
        const portInput = document.getElementById('proxy-port');
        const usernameInput = document.getElementById('proxy-username');
        const passwordInput = document.getElementById('proxy-password');
        const statusBadge = document.getElementById('proxy-status-badge');

        if (enabledCheck) {
            enabledCheck.checked = !!data.enabled;
            this.updateBadgeUI(data.enabled);
        }
        if (rotationCheck) {
            rotationCheck.checked = !!data.rotation;
        }
        if (typeSelect) typeSelect.value = data.type || 'http';
        if (hostInput) hostInput.value = data.host || '';
        if (portInput) portInput.value = data.port || '';
        if (usernameInput) usernameInput.value = data.username || '';
        if (passwordInput) passwordInput.value = data.password || '';
    },

    populatePoolUI() {
        const countSpan = document.getElementById('pool-count');
        const listDiv = document.getElementById('proxy-pool-list');
        if (countSpan) countSpan.textContent = this.pool.length;
        if (!listDiv) return;

        if (this.pool.length === 0) {
            listDiv.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 16px; font-size: 0.82rem;">暂无可用代理 IP</div>`;
            return;
        }

        listDiv.innerHTML = this.pool.map((item, idx) => {
            const authStr = item.username ? `${item.username}@` : '';
            const desc = `${item.type}://${authStr}${item.host}:${item.port}`;
            return `
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 0.82rem; color: var(--text-primary);">
                    <div style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: monospace;">
                        ${idx + 1}. ${desc}
                    </div>
                    <button class="btn btn-danger btn-sm" onclick="ProxyPage.removeFromPool(${idx})" style="padding: 2px 6px; font-size: 0.75rem;">删除</button>
                </div>
            `;
        }).join('');
    },

    updateBadgeUI(enabled) {
        const badge = document.getElementById('proxy-status-badge');
        if (!badge) return;
        if (enabled) {
            badge.className = 'badge badge-success';
            badge.textContent = '已启用';
        } else {
            badge.className = 'badge badge-secondary';
            badge.textContent = '已关闭';
        }
    },

    async toggleProxy(enabled) {
        this.updateBadgeUI(enabled);
        try {
            await API.proxy.saveConfig({ enabled });
            Toast.success(enabled ? '代理服务已开启' : '代理服务已关闭');
        } catch (err) {
            // error
        }
    },

    async toggleRotation(rotation) {
        try {
            await API.proxy.saveConfig({ rotation });
            Toast.success(rotation ? '多代理轮换已开启' : '多代理轮换已关闭');
        } catch (err) {
            // error
        }
    },

    async testSingleProxy() {
        const typeSelect = document.getElementById('proxy-type');
        const hostInput = document.getElementById('proxy-host');
        const portInput = document.getElementById('proxy-port');
        const usernameInput = document.getElementById('proxy-username');
        const passwordInput = document.getElementById('proxy-password');

        const host = hostInput.value.trim();
        const port = portInput.value.trim();

        if (!host || !port) {
            Toast.warning('请输入代理服务器主机和端口');
            return;
        }

        const btn = document.getElementById('btn-test-proxy');
        btn.disabled = true;
        btn.innerHTML = '<div class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></div> 测试中...';

        const testData = {
            type: typeSelect.value,
            host,
            port,
            username: usernameInput.value.trim(),
            password: passwordInput.value,
        };

        try {
            const data = await API.proxy.test(testData);
            if (data.success) {
                Toast.success(`${data.message} (${data.latency}ms)`);
            } else {
                Toast.error(data.message);
            }
        } catch (err) {
            Toast.error('测试代理异常');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '⚡ 测试连接';
        }
    },

    async saveSingleProxy() {
        const typeSelect = document.getElementById('proxy-type');
        const hostInput = document.getElementById('proxy-host');
        const portInput = document.getElementById('proxy-port');
        const usernameInput = document.getElementById('proxy-username');
        const passwordInput = document.getElementById('proxy-password');

        const config = {
            type: typeSelect.value,
            host: hostInput.value.trim(),
            port: portInput.value.trim(),
            username: usernameInput.value.trim(),
            password: passwordInput.value,
        };

        try {
            await API.proxy.saveConfig(config);
            Toast.success('单代理配置已保存');
            await this.loadConfig();
        } catch (err) {
            // error
        }
    },

    async addToPool() {
        const poolTypeSelect = document.getElementById('pool-type');
        const poolUrlInput = document.getElementById('pool-url');
        const raw = poolUrlInput.value.trim();

        if (!raw) {
            Toast.warning('请输入代理 IP 地址');
            return;
        }

        // 解析格式: [user:pass@]host:port
        let type = poolTypeSelect.value;
        let host = '';
        let port = '';
        let username = '';
        let password = '';

        try {
            let working = raw;
            if (working.includes('@')) {
                const parts = working.split('@');
                const authParts = parts[0].split(':');
                username = authParts[0];
                password = authParts[1] || '';
                working = parts[1];
            }
            const hostParts = working.split(':');
            host = hostParts[0];
            port = hostParts[1] || '80';
        } catch (err) {
            Toast.error('代理格式解析失败，请检查格式');
            return;
        }

        if (!host || !port) {
            Toast.warning('格式有误，必须包含主机和端口');
            return;
        }

        try {
            await API.proxy.addToPool({ type, host, port, username, password });
            Toast.success('已添加到代理池');
            poolUrlInput.value = '';
            await this.loadPool();
        } catch (err) {
            // error
        }
    },

    async removeFromPool(index) {
        try {
            await API.proxy.removeFromPool(index);
            Toast.success('已从代理池删除');
            await this.loadPool();
        } catch (err) {
            // error
        }
    },
};
