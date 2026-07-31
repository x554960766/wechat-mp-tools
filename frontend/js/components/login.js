/**
 * 账号池页面组件（由原 LoginPage / 扫码登录页改造而来）
 * 路由 key 仍为 'login'，保持向后兼容
 */
const LoginPage = {
    _pollTimer: null,
    _eventTimer: null,

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

    formatCooldown(cooldownUntil) {
        if (!cooldownUntil) return '';
        const remaining = Math.max(0, Math.ceil((cooldownUntil * 1000 - Date.now()) / 60000));
        return remaining > 0 ? `${remaining}分钟` : '即将恢复';
    },

    statusLabel(status) {
        const map = {
            active: '正常',
            cooldown: '冷却中',
            banned: '已踢出 · 风控',
            invalid: '已踢出 · 登录失效',
        };
        return map[status] || status;
    },

    statusColor(status) {
        const map = {
            active: 'var(--success)',
            cooldown: 'var(--warning)',
            banned: 'var(--error)',
            invalid: 'var(--error)',
        };
        return map[status] || 'var(--text-muted)';
    },

    render() {
        return `
            <div class="page-header" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h2 class="page-title">账号池</h2>
                    <p class="page-description">管理微信读书采集账号，支持多账号自动轮换</p>
                </div>
                <button class="btn btn-primary" id="btn-add-account" onclick="LoginPage.startLogin()">
                    <svg viewBox="0 0 24 24" fill="none" width="18" height="18">
                        <line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                    </svg>
                    添加账号
                </button>
            </div>

            <div id="pool-summary" style="margin-bottom: 20px;"></div>
            <div id="pool-login-status" style="margin-bottom: 20px;"></div>
            <div id="pool-accounts-grid" class="animate-fade-in"></div>
        `;
    },

    async init() {
        await this.loadAccounts();
        this._startEventPolling();
    },

    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
        if (this._eventTimer) {
            clearInterval(this._eventTimer);
            this._eventTimer = null;
        }
    },

    _startEventPolling() {
        if (this._eventTimer) clearInterval(this._eventTimer);
        this._eventTimer = setInterval(async () => {
            try {
                const data = await API.accountPool.events();
                if (data.events && data.events.length > 0) {
                    for (const ev of data.events) {
                        Toast.warning(`账号【${ev.nickname || '未知'}】${ev.reason}，已被移出账号池`);
                    }
                    this.loadAccounts();
                }
            } catch (e) { /* silent */ }
        }, 15000);
    },

    async loadAccounts() {
        try {
            const [poolData, summaryData] = await Promise.all([
                API.accountPool.list(),
                API.accountPool.summary(),
            ]);
            this.renderSummary(summaryData);
            this.renderGrid(poolData.accounts || []);
        } catch (err) {
            const grid = document.getElementById('pool-accounts-grid');
            if (grid) grid.innerHTML = `<div style="text-align:center; color: var(--text-muted); padding: 40px;">加载账号列表失败</div>`;
        }
    },

    renderSummary(summary) {
        const el = document.getElementById('pool-summary');
        if (!el) return;
        const { total = 0, active = 0, cooldown = 0, banned = 0, invalid = 0 } = summary || {};
        el.innerHTML = `
            <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                <span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: rgba(7,193,96,0.1); color: #07c160; font-weight: 600;">
                    可用 ${active}
                </span>
                ${cooldown > 0 ? `<span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: rgba(255,165,0,0.1); color: var(--warning); font-weight: 600;">
                    冷却 ${cooldown}
                </span>` : ''}
                ${(banned + invalid) > 0 ? `<span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: rgba(255,59,48,0.1); color: var(--error); font-weight: 600;">
                    已踢出 ${banned + invalid}
                </span>` : ''}
                <span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: var(--bg-tertiary); color: var(--text-muted);">
                    共 ${total} 个账号
                </span>
            </div>
        `;
    },

    renderGrid(accounts) {
        const grid = document.getElementById('pool-accounts-grid');
        if (!grid) return;

        if (accounts.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" style="text-align: center; padding: 60px 24px;">
                    <div style="font-size: 3rem; margin-bottom: 16px; opacity: 0.4;">🔐</div>
                    <h3 style="color: var(--text-primary); margin-bottom: 8px;">账号池为空</h3>
                    <p style="color: var(--text-muted); margin-bottom: 24px;">点击「添加账号」扫码登录，绑定微信读书采集账号</p>
                    <button class="btn btn-primary" onclick="LoginPage.startLogin()">
                        <svg viewBox="0 0 24 24" fill="none" width="18" height="18">
                            <line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                        添加账号
                    </button>
                </div>
            `;
            return;
        }

        grid.innerHTML = `
            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px;">
                ${accounts.map(acc => this._renderCard(acc)).join('')}
            </div>
        `;
    },

    _renderCard(acc) {
        const statusColor = this.statusColor(acc.status);
        const statusText = this.statusLabel(acc.status);
        const remaining = this.formatRemaining(acc.remaining_seconds);
        const isKicked = acc.status === 'banned' || acc.status === 'invalid';
        const isCooldown = acc.status === 'cooldown';
        const initial = (acc.nickname || '?').charAt(0);

        let extraInfo = '';
        if (isCooldown) {
            extraInfo = `<div style="font-size: 0.8rem; color: var(--warning);">冷却剩余: ${this.formatCooldown(acc.cooldown_until)}</div>`;
        }
        if (acc.last_error && isKicked) {
            extraInfo += `<div style="font-size: 0.8rem; color: var(--error); margin-top: 4px; word-break: break-all;">${this._esc(acc.last_error)}</div>`;
        }

        return `
            <div style="
                background: var(--bg-card);
                border: 1px solid ${isKicked ? 'rgba(255,59,48,0.25)' : isCooldown ? 'rgba(255,165,0,0.25)' : 'var(--border-color)'};
                border-radius: 12px;
                padding: 20px;
                transition: box-shadow 0.2s, transform 0.2s;
                position: relative;
            " onmouseenter="this.style.boxShadow='var(--shadow-md)';this.style.transform='translateY(-2px)'"
              onmouseleave="this.style.boxShadow='none';this.style.transform='none'">

                <!-- 状态点 -->
                <div style="position: absolute; top: 16px; right: 16px; display: flex; align-items: center; gap: 6px;">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background: ${statusColor}; display: inline-block;
                        ${acc.status === 'active' ? 'box-shadow: 0 0 6px rgba(7,193,96,0.5);' : ''}
                    "></span>
                    <span style="font-size: 0.75rem; color: ${statusColor}; font-weight: 600;">${statusText}</span>
                </div>

                <!-- 头像 + 昵称 -->
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px;">
                    ${acc.avatar
                        ? `<img src="${acc.avatar}" alt="" style="width: 44px; height: 44px; border-radius: 50%; border: 2px solid white; box-shadow: var(--shadow-sm); object-fit: cover;"
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                           <div style="display: none; width: 44px; height: 44px; border-radius: 50%; background: #07c160; color: white; align-items: center; justify-content: center; font-size: 1.2rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                        : `<div style="width: 44px; height: 44px; border-radius: 50%; background: #07c160; color: white; display: flex; align-items: center; justify-content: center; font-size: 1.2rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                    }
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-weight: 700; color: var(--text-primary); font-size: 1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${this._esc(acc.nickname)}</div>
                        <div style="font-size: 0.78rem; color: var(--text-muted); font-family: monospace;">${acc.token_preview || ''}</div>
                    </div>
                </div>

                <!-- 信息行 -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; font-size: 0.82rem; color: var(--text-secondary); margin-bottom: 12px;">
                    <div>失败: <strong>${acc.failures}</strong></div>
                    <div>风控: <strong>${acc.risk_hits}</strong></div>
                    <div style="grid-column: span 2;">
                        有效期: <strong style="color: ${acc.remaining_seconds > 86400 ? 'var(--success)' : acc.remaining_seconds > 0 ? 'var(--warning)' : 'var(--error)'};">${remaining}</strong>
                    </div>
                </div>

                ${extraInfo}

                <!-- 操作按钮 -->
                <div style="display: flex; gap: 8px; margin-top: 12px;">
                    ${isKicked ? `
                        <button class="btn btn-primary btn-sm" onclick="LoginPage.startLogin()" style="flex: 1; font-size: 0.8rem;">重新登录</button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="LoginPage.removeAccount('${acc.id}', '${this._esc(acc.nickname)}')" style="font-size: 0.8rem; ${isKicked ? '' : 'margin-left: auto;'}">
                        删除
                    </button>
                </div>
            </div>
        `;
    },

    _esc(s) {
        if (!s) return '';
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    },

    async startLogin() {
        const btn = document.getElementById('btn-add-account');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> 正在启动...';
        }

        const statusEl = document.getElementById('pool-login-status');
        if (statusEl) {
            statusEl.innerHTML = `
                <div style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px; text-align: center;">
                    <div class="spinner" style="margin: 0 auto 12px;"></div>
                    <p style="color: var(--text-primary); font-weight: 600;">正在请求微信读书扫码登录...</p>
                    <button class="btn btn-secondary btn-sm" style="margin-top: 12px;" onclick="LoginPage.cancelLogin()">取消</button>
                </div>
            `;
        }

        try {
            await API.auth.login();
            Toast.info('已请求扫码链接，正在等待扫码确认...');
            this.startStatusPolling();
        } catch (err) {
            Toast.error('启动登录失败: ' + err.message);
            this._resetAddButton();
            if (statusEl) statusEl.innerHTML = '';
        }
    },

    startStatusPolling() {
        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = setInterval(async () => {
            try {
                const data = await API.auth.status();
                const loginState = data.login_state || {};

                const statusEl = document.getElementById('pool-login-status');
                if (!statusEl) return;

                if (loginState.status === 'scanning') {
                    const scanUrl = loginState.qrcode || '';
                    const qrImgUrl = scanUrl ? `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodeURIComponent(scanUrl)}` : '';
                    statusEl.innerHTML = `
                        <div style="background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 24px; text-align: center; max-width: 420px; margin: 0 auto; box-shadow: var(--shadow-md);">
                            <p style="color: var(--text-primary); font-weight: 700; font-size: 1.1rem; margin-bottom: 6px;">📱 请使用手机微信扫码登录</p>
                            <p style="color: var(--text-muted); font-size: 0.85rem; margin-bottom: 16px;">打开手机微信 -> 扫一扫，扫描下方二维码并在手机上确认登录</p>
                            
                            ${qrImgUrl ? `
                                <div style="background: white; padding: 12px; border-radius: 12px; display: inline-block; box-shadow: var(--shadow-sm); border: 1px solid #eee;">
                                    <img src="${qrImgUrl}" alt="微信扫码二维码" style="width: 220px; height: 220px; display: block;" />
                                </div>
                            ` : `
                                <div class="spinner" style="margin: 20px auto;"></div>
                            `}
                            
                            <div style="margin-top: 16px; font-size: 0.82rem; color: var(--text-secondary);">
                                <span class="spinner-inline" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 4px;"></span>
                                正在等待扫码确认...
                            </div>
                            <button class="btn btn-secondary btn-sm" style="margin-top: 16px;" onclick="LoginPage.cancelLogin()">取消登录</button>
                        </div>
                    `;
                } else if (loginState.status === 'success') {
                    statusEl.innerHTML = `
                        <div style="background: rgba(7,193,96,0.05); border: 1px solid rgba(7,193,96,0.2); border-radius: 12px; padding: 20px; text-align: center;">
                            <p style="color: var(--success); font-weight: 600;">✅ ${loginState.message}</p>
                        </div>
                    `;
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetAddButton();
                    setTimeout(() => {
                        this.loadAccounts();
                        if (statusEl) statusEl.innerHTML = '';
                        App.checkAuthStatus();
                    }, 1000);
                } else if (loginState.status === 'failed') {
                    statusEl.innerHTML = `
                        <div style="background: rgba(255,59,48,0.05); border: 1px solid rgba(255,59,48,0.2); border-radius: 12px; padding: 20px; text-align: center;">
                            <p style="color: var(--error); font-weight: 600;">❌ ${loginState.message}</p>
                            <button class="btn btn-primary btn-sm" style="margin-top: 12px;" onclick="LoginPage.startLogin()">重新尝试</button>
                        </div>
                    `;
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetAddButton();
                } else if (loginState.status === 'idle') {
                    statusEl.innerHTML = '';
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetAddButton();
                }
            } catch (err) { /* silent */ }
        }, 2000);
    },

    _resetAddButton() {
        const btn = document.getElementById('btn-add-account');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" width="18" height="18">
                    <line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                    <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                </svg>
                添加账号
            `;
        }
    },

    async cancelLogin() {
        try {
            await API.auth.cancel();
            Toast.success('已取消登录流程');
            if (this._pollTimer) {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
            }
            this._resetAddButton();
            const statusEl = document.getElementById('pool-login-status');
            if (statusEl) statusEl.innerHTML = '';
        } catch (err) {
            Toast.error('取消失败: ' + err.message);
        }
    },

    async removeAccount(id, nickname) {
        Modal.confirm('删除账号', `确定要从账号池中删除「${nickname}」吗？`, async () => {
            try {
                await API.accountPool.remove(id);
                Toast.success('已删除');
                this.loadAccounts();
                App.checkAuthStatus();
            } catch (err) {
                Toast.error('删除失败: ' + err.message);
            }
        });
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
                await LoginPage.loadAccounts();
            } catch (err) {
                Toast.error('退出失败');
            }
        });
    },
};
