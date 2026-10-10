/**
 * 微信读书账号池页面组件
 * 路由 key: 'login'
 */
const LoginPage = {
    _pollTimer: null,
    _eventTimer: null,
    _accounts: [],

    formatDate(timestamp) {
        return timestamp
            ? new Date(timestamp * 1000).toLocaleString('zh-CN')
            : '未知';
    },

    statusLabel(status) {
        const map = {
            active: '正常运行',
            cooldown: '冷却中',
            banned: '已失效 · 风控',
            invalid: '已失效 · 登录过期',
        };
        return map[status] || status;
    },

    statusColor(status) {
        const map = {
            active: 'var(--success, #07c160)',
            cooldown: 'var(--warning, #ff9900)',
            banned: 'var(--error, #e53e3e)',
            invalid: 'var(--error, #e53e3e)',
        };
        return map[status] || 'var(--text-muted, #888)';
    },

    render() {
        return `
            <div class="page-header" style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 20px;">
                <div>
                    <h2 class="page-title" style="margin: 0;">微信读书账号池</h2>
                    <p class="page-description" style="margin-top: 6px; color: var(--text-secondary);">管理您的微信读书账号池与登录状态</p>
                </div>
                <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                    <button class="btn btn-secondary" onclick="LoginPage.loadAccounts()" style="font-size: 0.85rem; padding: 8px 14px; display: flex; align-items: center; gap: 6px;">
                        🔄 刷新列表
                    </button>
                    <button class="btn btn-primary" id="btn-add-account" onclick="LoginPage.startLogin()" style="font-weight: 600; font-size: 0.85rem; padding: 8px 18px; display: flex; align-items: center; gap: 6px;">
                        ➕ 扫码添加账号
                    </button>
                </div>
            </div>

            <div id="pool-summary" style="margin-bottom: 20px;"></div>
            <div id="pool-accounts-grid" class="animate-fade-in"></div>

            <!-- 独立扫码模态弹窗 (Modal)，悬浮全屏居中展示，完全不影响页面任何已有排版 -->
            <div id="weread-qr-modal" style="display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); z-index: 9999; align-items: center; justify-content: center; backdrop-filter: blur(4px); padding: 20px;">
                <div style="background: var(--bg-card, #ffffff); border: 1px solid var(--border-color, #e0e0e0); border-radius: 16px; width: 100%; max-width: 380px; padding: 24px; text-align: center; box-shadow: 0 20px 45px rgba(0,0,0,0.3); position: relative;">
                    <button onclick="LoginPage.cancelLogin()" style="position: absolute; top: 14px; right: 14px; background: none; border: none; font-size: 1.25rem; color: var(--text-muted, #999); cursor: pointer; padding: 4px 8px; border-radius: 6px; line-height: 1;" title="关闭">✕</button>
                    <h3 style="margin: 0 0 6px; font-size: 1.15rem; color: var(--text-primary); font-weight: 700;">📱 微信读书扫码登录</h3>
                    <p style="margin: 0 0 16px; font-size: 0.84rem; color: var(--text-secondary);">使用手机微信「扫一扫」并确认授权</p>
                    
                    <div id="weread-qr-box" style="min-height: 220px; display: flex; align-items: center; justify-content: center;">
                        <div class="spinner"></div>
                    </div>

                    <div id="weread-qr-msg" style="margin-top: 14px; font-size: 0.85rem; color: var(--text-secondary); min-height: 22px;">
                        正在加载官方授权二维码...
                    </div>

                    <div style="margin-top: 18px; display: flex; gap: 10px; justify-content: center;">
                        <button class="btn btn-secondary btn-sm" onclick="LoginPage.cancelLogin()" style="padding: 6px 18px;">取消</button>
                    </div>
                </div>
            </div>
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
                        Toast.warning(`账号【${ev.nickname || '未知'}】${ev.reason}`);
                    }
                    await this.loadAccounts();
                }
            } catch (e) { /* silent */ }
        }, 5000);
    },

    async loadAccounts() {
        try {
            const [poolData, summaryData] = await Promise.all([
                API.accountPool.list(),
                API.accountPool.summary(),
            ]);
            this._accounts = poolData.accounts || [];
            this.renderSummary(summaryData);
            this.renderGrid(this._accounts);
        } catch (err) {
            const grid = document.getElementById('pool-accounts-grid');
            if (grid) grid.innerHTML = `<div style="text-align:center; color: var(--text-muted); padding: 40px;">加载账号列表失败: ${err.message}</div>`;
        }
    },

    renderSummary(summary) {
        const el = document.getElementById('pool-summary');
        if (!el) return;
        const { total = 0, active = 0, cooldown = 0, banned = 0, invalid = 0 } = summary || {};
        el.innerHTML = `
            <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                <span style="font-size: 0.85rem; padding: 5px 14px; border-radius: 20px; background: rgba(7,193,96,0.12); color: #07c160; font-weight: 600;">
                    🟢 可用账号 ${active}
                </span>
                ${cooldown > 0 ? `<span style="font-size: 0.85rem; padding: 5px 14px; border-radius: 20px; background: rgba(255,165,0,0.12); color: var(--warning); font-weight: 600;">
                    🟡 冷却中 ${cooldown}
                </span>` : ''}
                ${(banned + invalid) > 0 ? `<span style="font-size: 0.85rem; padding: 5px 14px; border-radius: 20px; background: rgba(255,59,48,0.12); color: var(--error); font-weight: 600;">
                    🔴 已失效 ${banned + invalid}
                </span>` : ''}
                <span style="font-size: 0.85rem; padding: 5px 14px; border-radius: 20px; background: var(--bg-tertiary, #f3f4f6); color: var(--text-secondary); font-weight: 500;">
                    共 ${total} 个微信读书账号
                </span>
            </div>
        `;
    },

    renderGrid(accounts) {
        const grid = document.getElementById('pool-accounts-grid');
        if (!grid) return;

        if (accounts.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" style="text-align: center; padding: 60px 24px; background: var(--bg-card); border-radius: 12px; border: 1px dashed var(--border-color);">
                    <div style="font-size: 3rem; margin-bottom: 16px; opacity: 0.6;">📖</div>
                    <h3 style="color: var(--text-primary); margin-bottom: 8px;">微信读书账号池为空</h3>
                    <p style="color: var(--text-muted); margin-bottom: 24px; max-width: 480px; margin-left: auto; margin-right: auto; line-height: 1.6;">
                        点击右上角「➕ 扫码添加账号」，使用手机微信扫码授权登录，系统将自动录入并静默维护换票凭证。
                    </p>
                    <button class="btn btn-primary" onclick="LoginPage.startLogin()" style="display: inline-flex; align-items: center; gap: 8px; font-weight: 600; padding: 10px 20px;">
                        ➕ 立即扫码添加
                    </button>
                </div>
            `;
            return;
        }

        grid.innerHTML = `
            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 18px;">
                ${accounts.map(acc => this._renderCard(acc)).join('')}
            </div>
        `;
    },

    _renderCard(acc) {
        const statusColor = this.statusColor(acc.status);
        const statusText = this.statusLabel(acc.status);
        const isKicked = acc.status === 'banned' || acc.status === 'invalid';
        const initial = (acc.nickname || '?').charAt(0);

        return `
            <div style="
                background: var(--bg-card);
                border: 1px solid ${isKicked ? 'rgba(255,59,48,0.25)' : 'var(--border-color)'};
                border-radius: 12px;
                padding: 20px;
                transition: box-shadow 0.2s, transform 0.2s;
                position: relative;
            " onmouseenter="this.style.boxShadow='var(--shadow-md)';this.style.transform='translateY(-2px)'"
              onmouseleave="this.style.boxShadow='none';this.style.transform='none'">

                <!-- 状态徽标 -->
                <div style="position: absolute; top: 16px; right: 16px; display: flex; align-items: center; gap: 6px;">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background: ${statusColor}; display: inline-block;
                        ${acc.status === 'active' ? 'box-shadow: 0 0 6px rgba(7,193,96,0.6);' : ''}
                    "></span>
                    <span style="font-size: 0.78rem; color: ${statusColor}; font-weight: 600;">${statusText}</span>
                </div>

                <!-- 头像 + 昵称 -->
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px;">
                    ${acc.avatar
                        ? `<img src="${acc.avatar}" alt="" style="width: 46px; height: 46px; border-radius: 50%; border: 2px solid white; box-shadow: var(--shadow-sm); object-fit: cover;"
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                           <div style="display: none; width: 46px; height: 46px; border-radius: 50%; background: #07c160; color: white; align-items: center; justify-content: center; font-size: 1.2rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                        : `<div style="width: 46px; height: 46px; border-radius: 50%; background: #07c160; color: white; display: flex; align-items: center; justify-content: center; font-size: 1.2rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                    }
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-weight: 700; color: var(--text-primary); font-size: 1.05rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${this._esc(acc.nickname || '微信读书用户')}</div>
                        <div style="font-size: 0.76rem; color: var(--text-muted); font-family: monospace;">VID: ${acc.vid || acc.id}</div>
                    </div>
                </div>

                <!-- 统计行 -->
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; font-size: 0.82rem; color: var(--text-secondary); margin-bottom: 14px; background: var(--bg-tertiary, rgba(0,0,0,0.02)); padding: 10px 12px; border-radius: 8px;">
                    <div>绑定设备: <strong style="color: #07c160;">${this._esc(acc.deviceName || 'Xiaomi 14')}</strong></div>
                    <div>换票算法: <strong>S-Box 纯算</strong></div>
                    <div>成功换票: <strong>${acc.success_refreshes || 0}</strong> 次</div>
                    <div>连续失败: <strong>${acc.failures || 0}</strong> 次</div>
                    ${acc.save_time ? `<div style="grid-column: span 2; color: var(--text-muted); font-size: 0.74rem;">更新时间: ${this.formatDate(acc.save_time)}</div>` : ''}
                </div>

                ${acc.last_error ? `<div style="font-size: 0.78rem; color: var(--error); margin-bottom: 10px; word-break: break-all;">最近异常: ${this._esc(acc.last_error)}</div>` : ''}

                <!-- 操作按钮 -->
                <div style="display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap;">
                    <button class="btn btn-secondary btn-sm" onclick="LoginPage.refreshAccount('${acc.id}')" style="font-size: 0.8rem; display: flex; align-items: center; gap: 4px;" title="使用 RefreshToken 立即重新换取最新 AccessToken">
                        🔄 换新 Token
                    </button>
                    <button class="btn btn-secondary btn-sm" onclick="LoginPage.verifyAccount('${acc.id}')" style="font-size: 0.8rem; display: flex; align-items: center; gap: 4px;" title="向微信读书发起验证接口确认凭证活性">
                        🔍 验证
                    </button>
                    ${isKicked ? `
                        <button class="btn btn-primary btn-sm" onclick="LoginPage.startLogin()" style="font-size: 0.8rem;">重新扫码</button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="LoginPage.removeAccount('${acc.id}')" style="font-size: 0.8rem; margin-left: auto;">
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

    async refreshAccount(id) {
        Toast.info('正在为账号执行原生算法静默换票...');
        try {
            const data = await API.post(`/api/account-pool/${id}/refresh`);
            if (data.success) {
                Toast.success('Token 刷新成功！已换取最新 AccessToken 并入库');
            } else {
                Toast.warning('刷新提示: ' + (data.message || data.error));
            }
            await this.loadAccounts();
        } catch (e) {
            Toast.error('换票失败: ' + e.message);
        }
    },

    async verifyAccount(id) {
        Toast.info('正在验证账号有效性...');
        try {
            const data = await API.post(`/api/account-pool/${id}/verify`);
            if (data.valid) {
                Toast.success(`验证通过: ${data.message}`);
            } else {
                Toast.warning(`验证结果: ${data.message}`);
            }
            await this.loadAccounts();
        } catch (e) {
            Toast.error('验证失败: ' + e.message);
        }
    },

    async startLogin() {
        const btn = document.getElementById('btn-add-account');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></div> 启动中...';
        }

        const modal = document.getElementById('weread-qr-modal');
        const qrBox = document.getElementById('weread-qr-box');
        const qrMsg = document.getElementById('weread-qr-msg');
        if (modal) {
            modal.style.display = 'flex';
        }
        if (qrBox) {
            qrBox.innerHTML = '<div class="spinner" style="margin: 30px auto;"></div>';
        }
        if (qrMsg) {
            qrMsg.innerHTML = '正在获取微信读书官方授权二维码...';
        }

        try {
            const data = await API.auth.login();
            if (data.login_state && data.login_state.qrcode) {
                this._renderQrImage(data.login_state.qrcode);
            }
            this.startStatusPolling();
        } catch (err) {
            Toast.error('获取登录二维码失败: ' + err.message);
            this._resetAddButton();
            if (qrMsg) {
                qrMsg.innerHTML = `<span style="color: var(--error, #e53e3e);">获取失败: ${err.message}</span>`;
            }
        }
    },

    _renderQrImage(rawUrl) {
        const qrBox = document.getElementById('weread-qr-box');
        const qrMsg = document.getElementById('weread-qr-msg');
        if (!qrBox) return;

        let imgSrc = rawUrl;
        if (!imgSrc.startsWith('data:') && !imgSrc.startsWith('http')) {
            imgSrc = 'data:image/jpeg;base64,' + imgSrc;
        }

        qrBox.innerHTML = `
            <div style="background: white; padding: 12px; border-radius: 14px; display: inline-block; box-shadow: 0 4px 16px rgba(0,0,0,0.08); border: 1px solid #eee;">
                <img src="${imgSrc}" alt="微信扫码授权" style="width: 210px; height: 210px; display: block; border-radius: 6px;" />
            </div>
        `;
        if (qrMsg) {
            qrMsg.innerHTML = '<span class="spinner-inline" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 4px;"></span>正在等待手机微信扫码授权...';
        }
    },

    startStatusPolling() {
        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = setInterval(async () => {
            try {
                const data = await API.auth.status();
                const loginState = data.login_state || {};
                const modal = document.getElementById('weread-qr-modal');
                const qrBox = document.getElementById('weread-qr-box');
                const qrMsg = document.getElementById('weread-qr-msg');
                if (!modal || modal.style.display === 'none') return;

                if (loginState.status === 'scanning') {
                    if (loginState.qrcode && qrBox && !qrBox.querySelector('img')) {
                        this._renderQrImage(loginState.qrcode);
                    }
                } else if (loginState.status === 'scanned') {
                    if (qrMsg) {
                        qrMsg.innerHTML = '<span style="color: var(--warning, #ff9900); font-weight: bold; font-size: 0.95rem;">📱 已扫码，请在手机上点击【确认登录】</span>';
                    }
                } else if (loginState.status === 'success') {
                    if (qrBox) {
                        qrBox.innerHTML = '<div style="font-size: 3.5rem; margin: 15px 0;">🎉</div>';
                    }
                    if (qrMsg) {
                        qrMsg.innerHTML = `<span style="color: var(--success, #07c160); font-weight: 700; font-size: 1rem;">${loginState.message || '登录成功！'}</span>`;
                    }
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetAddButton();
                    Toast.success('微信读书账号登录成功！');
                    setTimeout(() => {
                        modal.style.display = 'none';
                        this.loadAccounts();
                    }, 1200);
                } else if (loginState.status === 'failed') {
                    if (qrBox) {
                        qrBox.innerHTML = '<div style="font-size: 3rem; margin: 15px 0; color: #e53e3e;">❌</div>';
                    }
                    if (qrMsg) {
                        qrMsg.innerHTML = `<span style="color: var(--error, #e53e3e); font-weight: 600;">${loginState.message}</span>
                            <div style="margin-top: 10px;"><button class="btn btn-primary btn-sm" onclick="LoginPage.startLogin()">重新尝试</button></div>`;
                    }
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetAddButton();
                } else if (loginState.status === 'expired') {
                    if (qrBox) {
                        qrBox.innerHTML = '<div style="font-size: 3rem; margin: 15px 0; color: #ff9900;">⏳</div>';
                    }
                    if (qrMsg) {
                        qrMsg.innerHTML = `<span style="color: var(--warning, #ff9900); font-weight: 600;">二维码已过期</span>
                            <div style="margin-top: 10px;"><button class="btn btn-primary btn-sm" onclick="LoginPage.startLogin()">刷新二维码</button></div>`;
                    }
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetAddButton();
                } else if (loginState.status === 'idle') {
                    modal.style.display = 'none';
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    this._resetAddButton();
                }
            } catch (err) { /* silent */ }
        }, 1500);
    },

    _resetAddButton() {
        const btn = document.getElementById('btn-add-account');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '➕ 扫码添加账号';
        }
    },

    async cancelLogin() {
        try {
            await API.auth.cancel();
            Toast.info('已关闭登录弹窗');
            if (this._pollTimer) {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
            }
            this._resetAddButton();
            const modal = document.getElementById('weread-qr-modal');
            if (modal) {
                modal.style.display = 'none';
            }
        } catch (err) {
            Toast.error('操作失败: ' + err.message);
        }
    },

    async removeAccount(id) {
        const acc = (this._accounts || []).find(a => a.id === id);
        const name = acc ? (acc.nickname || '该账号') : '该账号';
        Modal.confirm('删除账号', `确定要从账号池中删除「${name}」吗？`, async () => {
            try {
                await API.accountPool.remove(id);
                Toast.success('已删除');
                await this.loadAccounts();
            } catch (err) {
                Toast.error('删除失败: ' + err.message);
            }
        });
    },
};
