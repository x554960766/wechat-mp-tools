/**
 * 账号池页面组件（由原 LoginPage / 扫码登录页升级而来）
 * 路由 key 仍为 'login'，保持系统向后兼容
 */
const LoginPage = {
    _pollTimer: null,
    _eventTimer: null,
    _currentFilter: 'all',
    _accountsCache: [],
    _verifyingIds: new Set(),
    _isVerifyingAll: false,

    formatDate(timestamp) {
        return timestamp
            ? new Date(timestamp * 1000).toLocaleString('zh-CN', {
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            })
            : '未知';
    },

    formatTimeAgo(timestamp) {
        if (!timestamp) return '未检测';
        const diff = Math.floor(Date.now() / 1000 - timestamp);
        if (diff < 30) return '刚刚';
        if (diff < 60) return `${diff}秒前`;
        if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`;
        const days = Math.floor(diff / 86400);
        return `${days}天前`;
    },

    formatUptime(saveTime) {
        if (!saveTime) return '新添加';
        const diff = Math.floor(Date.now() / 1000 - saveTime);
        const days = Math.floor(diff / 86400);
        const hours = Math.floor((diff % 86400) / 3600);
        if (days > 0) return `已添加 ${days} 天 ${hours} 小时`;
        if (hours > 0) return `已添加 ${hours} 小时`;
        return `已添加 ${Math.max(1, Math.floor(diff / 60))} 分钟`;
    },

    formatCooldown(cooldownUntil) {
        if (!cooldownUntil) return '';
        const remaining = Math.max(0, Math.ceil((cooldownUntil * 1000 - Date.now()) / 60000));
        return remaining > 0 ? `${remaining}分钟` : '即将恢复';
    },

    statusLabel(status) {
        const map = {
            active: '正常可用',
            cooldown: '冷却中',
            banned: '已踢出 · 风控',
            invalid: '已失效 · 需重登',
        };
        return map[status] || status;
    },

    statusColor(status) {
        const map = {
            active: '#07c160',
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
                    <h2 class="page-title">账号池管理</h2>
                    <p class="page-description">管理微信读书 / 微信公众平台采集账号，支持多账号智能调度与健康探活</p>
                </div>
                <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap;">
                    <button class="btn btn-secondary" id="btn-verify-all" onclick="LoginPage.verifyAllAccounts()">
                        <svg viewBox="0 0 24 24" fill="none" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">
                            <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                        一键检测全部
                    </button>
                    <button class="btn btn-primary" id="btn-add-account" onclick="LoginPage.startLogin()">
                        <svg viewBox="0 0 24 24" fill="none" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">
                            <line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                        添加账号
                    </button>
                </div>
            </div>

            <!-- 概要统计与筛选器 -->
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; margin-bottom: 20px;">
                <div id="pool-summary"></div>
                <div id="pool-filter-tabs" style="display: flex; gap: 6px; background: var(--bg-card); padding: 4px; border-radius: 8px; border: 1px solid var(--border-color);">
                    <button class="btn btn-sm btn-filter active" data-filter="all" onclick="LoginPage.setFilter('all')" style="padding: 4px 12px; font-size: 0.8rem; border-radius: 6px;">全部</button>
                    <button class="btn btn-sm btn-filter" data-filter="active" onclick="LoginPage.setFilter('active')" style="padding: 4px 12px; font-size: 0.8rem; border-radius: 6px;">正常</button>
                    <button class="btn btn-sm btn-filter" data-filter="cooldown" onclick="LoginPage.setFilter('cooldown')" style="padding: 4px 12px; font-size: 0.8rem; border-radius: 6px;">冷却中</button>
                    <button class="btn btn-sm btn-filter" data-filter="invalid" onclick="LoginPage.setFilter('invalid')" style="padding: 4px 12px; font-size: 0.8rem; border-radius: 6px;">异常/失效</button>
                </div>
            </div>

            <div id="pool-login-status" style="margin-bottom: 20px;"></div>
            <div id="pool-accounts-grid" class="animate-fade-in">
                <div class="loading-screen" style="min-height: 200px; text-align: center; padding: 40px;">
                    <div class="spinner" style="margin: 0 auto 12px;"></div>
                    <p style="color: var(--text-muted); font-size: 0.9rem;">正在加载账号池列表...</p>
                </div>
            </div>
        `;
    },

    async init() {
        await this.loadAccounts();
        this._startEventPolling();
    },

    async onShow() {
        await this.loadAccounts();
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
                        Toast.warning(`账号【${ev.nickname || '未知'}】${ev.reason}，已被移出调度`);
                    }
                    this.loadAccounts();
                }
            } catch (e) { /* silent */ }
        }, 15000);
    },

    setFilter(filter) {
        this._currentFilter = filter;
        document.querySelectorAll('#pool-filter-tabs .btn-filter').forEach(btn => {
            if (btn.getAttribute('data-filter') === filter) {
                btn.style.background = 'var(--primary)';
                btn.style.color = '#fff';
            } else {
                btn.style.background = 'transparent';
                btn.style.color = 'var(--text-secondary)';
            }
        });
        this.renderGrid(this._accountsCache);
    },

    async loadAccounts() {
        try {
            const [poolData, summaryData] = await Promise.all([
                API.accountPool.list(),
                API.accountPool.summary(),
            ]);
            this._accountsCache = poolData.accounts || [];
            this.renderSummary(summaryData);
            this.setFilter(this._currentFilter);
        } catch (err) {
            const grid = document.getElementById('pool-accounts-grid');
            if (grid) grid.innerHTML = `<div style="text-align:center; color: var(--text-muted); padding: 40px;">加载账号列表失败: ${err.message || ''}</div>`;
        }
    },

    renderSummary(summary) {
        const el = document.getElementById('pool-summary');
        if (!el) return;
        const { total = 0, active = 0, cooldown = 0, banned = 0, invalid = 0 } = summary || {};
        el.innerHTML = `
            <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
                <span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: rgba(7,193,96,0.12); color: #07c160; font-weight: 600;">
                    🟢 正常可用 ${active}
                </span>
                ${cooldown > 0 ? `<span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: rgba(255,165,0,0.12); color: var(--warning); font-weight: 600;">
                    🟡 冷却中 ${cooldown}
                </span>` : ''}
                ${(banned + invalid) > 0 ? `<span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: rgba(255,59,48,0.12); color: var(--error); font-weight: 600;">
                    🔴 异常/失效 ${banned + invalid}
                </span>` : ''}
                <span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-muted);">
                    共 ${total} 个账号
                </span>
            </div>
        `;
    },

    renderGrid(accounts) {
        const grid = document.getElementById('pool-accounts-grid');
        if (!grid) return;

        let filtered = accounts;
        if (this._currentFilter === 'active') {
            filtered = accounts.filter(a => a.status === 'active');
        } else if (this._currentFilter === 'cooldown') {
            filtered = accounts.filter(a => a.status === 'cooldown');
        } else if (this._currentFilter === 'invalid') {
            filtered = accounts.filter(a => a.status === 'invalid' || a.status === 'banned');
        }

        if (filtered.length === 0) {
            grid.innerHTML = `
                <div class="empty-state" style="text-align: center; padding: 60px 24px; background: var(--bg-card); border-radius: 12px; border: 1px dashed var(--border-color);">
                    <div style="font-size: 3rem; margin-bottom: 16px; opacity: 0.5;">🔐</div>
                    <h3 style="color: var(--text-primary); margin-bottom: 8px;">${this._currentFilter === 'all' ? '账号池暂无账号' : '该筛选条件下暂无账号'}</h3>
                    <p style="color: var(--text-muted); margin-bottom: 24px;">点击上方「添加账号」扫码登录，即可将微信读书账号加入池中自动轮换</p>
                    <button class="btn btn-primary" onclick="LoginPage.startLogin()">
                        <svg viewBox="0 0 24 24" fill="none" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">
                            <line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                            <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                        立即添加账号
                    </button>
                </div>
            `;
            return;
        }

        grid.innerHTML = `
            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px;">
                ${filtered.map(acc => this._renderCard(acc)).join('')}
            </div>
        `;
    },

    _renderCard(acc) {
        const statusColor = this.statusColor(acc.status);
        const statusText = this.statusLabel(acc.status);
        const isKicked = acc.status === 'banned' || acc.status === 'invalid';
        const isCooldown = acc.status === 'cooldown';
        const isVerifying = this._verifyingIds.has(acc.id);
        const initial = (acc.nickname || '?').charAt(0);

        // 标签：微信读书
        const typeBadge = `<span style="font-size: 0.72rem; padding: 2px 8px; border-radius: 4px; background: rgba(7,193,96,0.12); color: #07c160; font-weight: 600; display: inline-flex; align-items: center; gap: 3px;">
            📖 微信读书
        </span>`;

        // 备注或昵称
        const remarkDisplay = acc.remark ? `<span style="font-size: 0.8rem; color: var(--primary); background: rgba(7,193,96,0.08); padding: 1px 6px; border-radius: 4px; margin-left: 6px; font-weight: 500;">${this._esc(acc.remark)}</span>` : '';

        // 探活时间文字
        const verifyText = acc.last_verified_at ? `${this.formatTimeAgo(acc.last_verified_at)}验证` : '未检测';

        // 异常信息展示
        let alertHtml = '';
        if (isCooldown) {
            alertHtml = `
                <div style="font-size: 0.78rem; color: var(--warning); background: rgba(255,165,0,0.08); padding: 6px 10px; border-radius: 6px; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between;">
                    <span>⚠️ 触发风控冷却</span>
                    <span>剩余 ${this.formatCooldown(acc.cooldown_until)}</span>
                </div>
            `;
        } else if (acc.last_error && isKicked) {
            alertHtml = `
                <div style="font-size: 0.78rem; color: var(--error); background: rgba(255,59,48,0.08); padding: 6px 10px; border-radius: 6px; margin-bottom: 12px; word-break: break-all;">
                    ❌ ${this._esc(acc.last_error)}
                </div>
            `;
        }

        return `
            <div style="
                background: var(--bg-card);
                border: 1px solid ${isKicked ? 'rgba(255,59,48,0.3)' : isCooldown ? 'rgba(255,165,0,0.3)' : 'var(--border-color)'};
                border-radius: 12px;
                padding: 18px;
                transition: box-shadow 0.2s, transform 0.2s;
                position: relative;
                box-shadow: var(--shadow-sm);
            " onmouseenter="this.style.boxShadow='var(--shadow-md)';this.style.transform='translateY(-2px)'"
              onmouseleave="this.style.boxShadow='var(--shadow-sm)';this.style.transform='none'">

                <!-- 卡片顶部：类型标签 + 状态徽章 -->
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                    <div>${typeBadge}</div>
                    <div style="display: flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 600; color: ${statusColor};">
                        <span style="width: 8px; height: 8px; border-radius: 50%; background: ${statusColor}; display: inline-block;
                            ${acc.status === 'active' ? 'box-shadow: 0 0 6px rgba(7,193,96,0.6);' : ''}
                        "></span>
                        ${statusText}
                    </div>
                </div>

                <!-- 头像 + 昵称 + 备注 + VID -->
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 14px;">
                    ${acc.avatar
                        ? `<img src="${acc.avatar}" alt="" style="width: 44px; height: 44px; border-radius: 50%; border: 2px solid white; box-shadow: var(--shadow-sm); object-fit: cover;"
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                           <div style="display: none; width: 44px; height: 44px; border-radius: 50%; background: #07c160; color: white; align-items: center; justify-content: center; font-size: 1.2rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                        : `<div style="width: 44px; height: 44px; border-radius: 50%; background: #07c160; color: white; display: flex; align-items: center; justify-content: center; font-size: 1.2rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                    }
                    <div style="flex: 1; min-width: 0;">
                        <div style="display: flex; align-items: center; flex-wrap: wrap;">
                            <span style="font-weight: 700; color: var(--text-primary); font-size: 0.98rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 160px;" title="${this._esc(acc.nickname)}">${this._esc(acc.nickname)}</span>
                            ${remarkDisplay}
                        </div>
                        <div style="display: flex; gap: 8px; align-items: center; margin-top: 3px; font-size: 0.75rem; color: var(--text-muted);">
                            ${acc.vid ? `<span>VID: <code style="font-family: monospace;">${this._esc(acc.vid)}</code></span>` : ''}
                            <span>${acc.token_preview || ''}</span>
                        </div>
                    </div>
                </div>

                <!-- 健康与状态明细表格 -->
                <div style="background: var(--bg-tertiary, #f9fafb); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; font-size: 0.8rem; display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; color: var(--text-secondary);">
                    <div>
                        <span style="color: var(--text-muted);">探活检测:</span>
                        <strong style="color: ${acc.status === 'active' ? '#07c160' : 'var(--text-primary)'};">${verifyText}</strong>
                    </div>
                    <div>
                        <span style="color: var(--text-muted);">运行周期:</span>
                        <span>${this.formatUptime(acc.save_time)}</span>
                    </div>
                    <div>
                        <span style="color: var(--text-muted);">失败计数:</span>
                        <span style="color: ${acc.failures > 0 ? 'var(--warning)' : 'inherit'};">${acc.failures} 次</span>
                    </div>
                    <div>
                        <span style="color: var(--text-muted);">风控频次:</span>
                        <span style="color: ${acc.risk_hits > 0 ? 'var(--error)' : 'inherit'};">${acc.risk_hits} 次</span>
                    </div>
                </div>

                ${alertHtml}

                <!-- 操作按钮栏 -->
                <div style="display: flex; gap: 6px; align-items: center; justify-content: flex-end; flex-wrap: wrap;">
                    <button class="btn btn-secondary btn-sm" onclick="LoginPage.verifyAccount('${acc.id}', this)" ${isVerifying ? 'disabled' : ''} style="font-size: 0.78rem; padding: 4px 10px;">
                        ${isVerifying ? '<span class="spinner" style="width: 12px; height: 12px; margin-right: 4px; border-width: 2px;"></span>检测中' : '🔍 检测'}
                    </button>
                    <button class="btn btn-secondary btn-sm" onclick="LoginPage.editRemark('${acc.id}', '${this._esc(acc.remark || '')}', '${this._esc(acc.nickname)}')" style="font-size: 0.78rem; padding: 4px 10px;">
                        ✏️ 备注
                    </button>
                    ${isKicked || isCooldown ? `
                        <button class="btn btn-primary btn-sm" onclick="LoginPage.reviveAccount('${acc.id}', '${this._esc(acc.nickname)}')" style="font-size: 0.78rem; padding: 4px 10px;">
                            🔄 激活
                        </button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="LoginPage.removeAccount('${acc.id}', '${this._esc(acc.nickname)}')" style="font-size: 0.78rem; padding: 4px 10px;">
                        🗑️
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

    // ── 单账号探活检测 ──────────────────────────────
    async verifyAccount(id, btn) {
        this._verifyingIds.add(id);
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="width: 12px; height: 12px; margin-right: 4px; border-width: 2px;"></span>检测中...';
        }

        try {
            const res = await API.accountPool.verify(id);
            if (res.valid) {
                Toast.success(`账号【${res.nickname || ''}】检测通过: ${res.message}`);
            } else {
                Toast.warning(`账号【${res.nickname || ''}】检测反馈: ${res.message}`);
            }
            await this.loadAccounts();
            App.checkAuthStatus();
        } catch (err) {
            Toast.error('探活失败: ' + (err.message || '网络异常'));
        } finally {
            this._verifyingIds.delete(id);
            if (btn) btn.disabled = false;
        }
    },

    // ── 一键全量检测 ──────────────────────────────
    async verifyAllAccounts() {
        if (this._isVerifyingAll) return;
        this._isVerifyingAll = true;

        const btn = document.getElementById('btn-verify-all');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="width: 14px; height: 14px; margin-right: 4px; border-width: 2px;"></span> 正在批量检测...';
        }

        try {
            Toast.info('正在并发检测账号池全部凭证...');
            const data = await API.accountPool.verifyAll();
            const results = data.results || [];
            const validCount = results.filter(r => r.valid).length;
            const failCount = results.length - validCount;

            if (failCount === 0 && results.length > 0) {
                Toast.success(`全量检测完成：共 ${results.length} 个账号，全部正常有效 🟢`);
            } else if (results.length > 0) {
                Toast.warning(`全量检测完成：${validCount} 个正常，${failCount} 个异常需关注 ⚠️`);
            } else {
                Toast.info('账号池暂无账号');
            }

            await this.loadAccounts();
            App.checkAuthStatus();
        } catch (err) {
            Toast.error('批量检测异常: ' + err.message);
        } finally {
            this._isVerifyingAll = false;
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">
                        <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    一键检测全部
                `;
            }
        }
    },

    // ── 修改账号备注 ──────────────────────────────
    async editRemark(id, currentRemark, nickname) {
        Modal.prompt(
            `修改账号备注`,
            `为账号「${nickname}」设置便于区分的备注（例如：主要采集号、测试号1等）：`,
            currentRemark,
            async (newRemark) => {
                try {
                    await API.accountPool.update(id, { remark: newRemark });
                    Toast.success('备注已更新');
                    await this.loadAccounts();
                } catch (err) {
                    Toast.error('更新备注失败: ' + err.message);
                }
            }
        );
    },

    // ── 手动复活/重新激活 ──────────────────────────────
    async reviveAccount(id, nickname) {
        Modal.confirm('重新激活账号', `确定要将账号「${nickname}」重置为正常活跃状态并重新检测吗？`, async () => {
            try {
                const res = await API.accountPool.revive(id);
                if (res.verify_result && res.verify_result.valid) {
                    Toast.success(`账号「${nickname}」已激活并验证通过！`);
                } else {
                    Toast.warning(`账号「${nickname}」已重置状态: ${res.verify_result?.message || ''}`);
                }
                await this.loadAccounts();
                App.checkAuthStatus();
            } catch (err) {
                Toast.error('激活失败: ' + err.message);
            }
        });
    },

    // ── 登录流程 ──────────────────────────────────
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
                <svg viewBox="0 0 24 24" fill="none" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">
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
