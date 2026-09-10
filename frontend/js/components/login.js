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

    formatBizAge(seconds) {
        if (seconds === null || seconds === undefined) return '未知';
        if (seconds < 60) return '刚刚';
        if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟前`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}小时前`;
        return `${Math.floor(seconds / 86400)}天前`;
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
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <h2 class="page-title" style="margin: 0;">账号池</h2>
                        <span class="badge" id="pool-proxy-status-badge" style="font-size: 0.75rem; border-radius: 20px; padding: 2px 8px; font-weight: 600; background: rgba(0,0,0,0.08); color: var(--text-secondary);">检测中...</span>
                    </div>
                    <p class="page-description" style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 4px;">
                        <span>管理微信公众号采集凭证，通过系统代理与极速同步助手自动捕获续期</span>
                        <span id="pool-cert-status-badge" style="font-size: 0.8rem; color: var(--text-muted); display: inline-flex; align-items: center; gap: 4px;">
                            CA 证书: <strong style="color: #ff9900;">检测中</strong>
                        </span>
                    </p>
                </div>
                <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                    <!-- 定时同步开关 -->
                    <div style="display: flex; align-items: center; gap: 8px; background: var(--bg-secondary); padding: 6px 12px; border-radius: var(--radius-md); border: 1px solid var(--border-color);" title="开启后每 5 分钟自动检测并批量续期公众号凭证">
                        <label class="switch" style="margin: 0;">
                            <input type="checkbox" id="switch-auto-refresh" onchange="LoginPage.toggleAutoRefresh(this.checked)">
                            <span class="switch-slider"></span>
                        </label>
                        <span style="font-size: 0.84rem; color: var(--text-secondary); font-weight: 500;" id="label-auto-refresh">
                            定时自动同步 (已关闭)
                        </span>
                    </div>

                    <!-- 批量同步公众号凭证 / 去添加公众号 按钮 -->
                    <button class="btn btn-secondary" id="btn-manual-sync" onclick="LoginPage.handleSyncOrAddClick()" title="立即通过微信文件传输助手打开聚合页，批量同步全部公众号凭证" style="font-size: 0.85rem; padding: 8px 14px; display: flex; align-items: center; gap: 6px;">
                        🔄 批量同步公众号凭证
                    </button>

                    <!-- 一键安装/卸载证书 (保持与视频号一致) -->
                    <button class="btn btn-secondary" id="pool-btn-install-cert" onclick="LoginPage.installCert()" style="font-size: 0.85rem; padding: 8px 14px; display: flex; align-items: center; gap: 6px;" title="一键安装并信任本地 CA 根证书，确保微信抓包解密正常">
                        🛡️ 一键安装证书
                    </button>
                    <button class="btn btn-secondary" id="pool-btn-uninstall-cert" onclick="LoginPage.uninstallCert()" style="font-size: 0.85rem; padding: 8px 14px; display: none; align-items: center; gap: 6px;" title="从系统受信任根证书库中卸载 CA 证书">
                        🗑️ 一键卸载证书
                    </button>

                    <!-- 启动/关闭同步助手 (保持与视频号一致) -->
                    <button class="btn btn-primary" id="pool-btn-toggle-proxy" onclick="LoginPage.toggleProxy()" style="min-width: 140px; font-weight: 600; font-size: 0.85rem; padding: 8px 16px;">
                        🚀 启动同步助手
                    </button>
                </div>
            </div>

            <div id="pool-summary" style="margin-bottom: 20px;"></div>
            <div id="pool-login-status" style="margin-bottom: 20px;"></div>
            <div id="pool-accounts-grid" class="animate-fade-in"></div>
        `;
    },

    async init() {
        await Promise.all([
            this.loadAccounts(),
            this.loadAutoRefreshConfig(),
            this.checkProxyStatus(),
        ]);
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
                }
                // 无论是否有事件都刷新，确保 mitmproxy 后台静默更新凭证后 UI 能同步（5 秒粒度）
                await this.loadAccounts();
                await this.checkProxyStatus();
            } catch (e) { /* silent */ }
        }, 5000);
    },

    _savedAccountsCount: 0,

    async loadAccounts() {
        try {
            const [poolData, summaryData, accountsData] = await Promise.all([
                API.accountPool.list(),
                API.accountPool.summary(),
                API.accounts.list().catch(() => ({ accounts: [] })),
            ]);
            this._accounts = poolData.accounts || [];
            this._savedAccountsCount = (accountsData.accounts || []).length;
            this.renderSummary(summaryData);
            this.updateSyncButtonState();
            this.renderGrid(this._accounts);
        } catch (err) {
            const grid = document.getElementById('pool-accounts-grid');
            if (grid) grid.innerHTML = `<div style="text-align:center; color: var(--text-muted); padding: 40px;">加载账号列表失败</div>`;
        }
    },

    updateSyncButtonState() {
        const btn = document.getElementById('btn-manual-sync');
        if (!btn) return;
        if (this._savedAccountsCount === 0) {
            btn.className = 'btn btn-primary';
            btn.innerHTML = '➕ 去添加公众号';
            btn.title = '尚未在公众号管理中添加任何公众号，点击前往添加';
        } else {
            btn.className = 'btn btn-secondary';
            btn.innerHTML = '🔄 批量同步公众号凭证';
            btn.title = '立即通过微信文件传输助手打开聚合页，批量同步全部已收藏公众号凭证';
        }
    },

    handleSyncOrAddClick() {
        if (this._savedAccountsCount === 0) {
            Toast.info('💡 您尚未在公众号管理中添加公众号，正在为您跳转...');
            Router.navigate('accounts');
            return;
        }
        this.manualSync();
    },

    renderSummary(summary) {
        const el = document.getElementById('pool-summary');
        if (!el) return;
        const { total = 0, active = 0, cooldown = 0, banned = 0, invalid = 0 } = summary || {};
        // 公众号专属凭证统计（跨账号汇总，严格排除未命名凭证）
        const bizAll = (this._accounts || []).flatMap(a => a.biz_credentials || []).filter(b => b && b.name && b.name !== '未命名公众号' && b.name !== '公众号未命名');
        const bizFresh = bizAll.filter(b => b.fresh).length;
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
                    共 ${total} 个微信账号
                </span>
                ${bizAll.length ? `<span style="font-size: 0.85rem; padding: 4px 12px; border-radius: 20px; background: rgba(7,193,96,0.08); color: var(--text-secondary); font-weight: 600;">
                    📰 公众号凭证 ${bizAll.length}（新鲜 ${bizFresh}${bizAll.length - bizFresh > 0 ? ` · 待续期 ${bizAll.length - bizFresh}` : ''}）
                </span>` : ''}
            </div>
        `;
    },

    renderGrid(accounts) {
        const grid = document.getElementById('pool-accounts-grid');
        if (!grid) return;

        if (accounts.length === 0) {
            const hasSaved = this._savedAccountsCount > 0;
            grid.innerHTML = `
                <div class="empty-state" style="text-align: center; padding: 60px 24px;">
                    <div style="font-size: 3rem; margin-bottom: 16px; opacity: 0.4;">🔐</div>
                    <h3 style="color: var(--text-primary); margin-bottom: 8px;">账号池为空</h3>
                    <p style="color: var(--text-muted); margin-bottom: 24px; max-width: 480px; margin-left: auto; margin-right: auto; line-height: 1.6;">
                        ${hasSaved
                            ? '请确保顶部「启动同步助手」已开启，然后点击下方按钮唤起微信打开批量聚合页；或在微信中打开任意已关注公众号主页，凭证将自动录入。'
                            : '您尚未在「公众号管理」中添加任何公众号。请先点击下方按钮前往添加并收藏要同步的公众号。'
                        }
                    </p>
                    ${hasSaved
                        ? `<button class="btn btn-primary" onclick="LoginPage.manualSync()" style="display: inline-flex; align-items: center; gap: 8px; font-weight: 600;">
                               🔄 批量同步公众号凭证
                           </button>`
                        : `<button class="btn btn-primary" onclick="Router.navigate('accounts')" style="display: inline-flex; align-items: center; gap: 8px; font-weight: 600;">
                               ➕ 去添加公众号
                           </button>`
                    }
                </div>
            `;
            return;
        }

        // 公众号专属凭证独立成卡（严格排除未命名公众号），排在账号卡之前
        const bizCards = [];
        for (const acc of accounts) {
            for (const b of (acc.biz_credentials || [])) {
                if (!b || !b.name || b.name === '未命名公众号' || b.name === '公众号未命名') continue;
                bizCards.push(this._renderBizCard(b, acc));
            }
        }

        grid.innerHTML = `
            <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px;">
                ${bizCards.join('')}
                ${accounts.map(acc => this._renderCard(acc)).join('')}
            </div>
        `;
    },

    _renderBizCard(b, owner) {
        const fresh = !!b.fresh;
        const ready = !!b.getmsg_ready;
        // 已验证(可拉列表)的凭证按新鲜度绿/黄；未验证(仅文章页会话)灰色提示需开主页
        const color = ready ? (fresh ? 'var(--success)' : 'var(--warning)') : 'var(--text-muted)';
        const label = ready ? (fresh ? '会话新鲜' : '待续期') : '仅文章会话·需开主页';
        const safeName = (b.name || '').replace(/'/g, "\\'");
        const safeFakeid = (b.fakeid || b.biz || '').replace(/'/g, "\\'");
        const profileUrl = (b.profile_url || `https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=${encodeURIComponent(b.fakeid || b.biz || '')}#wechat_redirect`).replace(/'/g, "\\'");
        return `
            <div style="
                background: var(--bg-card);
                border: 1px solid ${ready && fresh ? 'var(--border-color)' : 'rgba(255,165,0,0.35)'};
                border-radius: 12px;
                padding: 16px 20px;
                position: relative;
            ">
                <div style="position: absolute; top: 14px; right: 16px; display: flex; align-items: center; gap: 6px;">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background: ${color}; display: inline-block;
                        ${ready && fresh ? 'box-shadow: 0 0 6px rgba(7,193,96,0.5);' : ''}"></span>
                    <span style="font-size: 0.75rem; color: ${color}; font-weight: 600;">${label}</span>
                </div>

                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                    <div style="width: 38px; height: 38px; border-radius: 10px; background: rgba(7,193,96,0.12); color: #07c160; display: flex; align-items: center; justify-content: center; font-size: 1.05rem; flex-shrink: 0;">📰</div>
                    <div style="flex: 1; min-width: 0;">
                        <div style="font-weight: 700; color: var(--text-primary); font-size: 0.95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${this._esc(b.name)}">${this._esc(b.name)}</div>
                        <div style="font-size: 0.72rem; color: var(--text-muted); font-family: monospace;">${this._esc(b.biz)}</div>
                    </div>
                </div>

                <div style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 10px;">
                    更新: <strong>${this.formatBizAge(b.age_seconds)}</strong>${b.has_key ? '' : ' · <span style="color: var(--error);">缺少 key</span>'}
                    <span style="color: var(--text-muted);"> · 归属: ${this._esc(owner.nickname || '微信账号')}</span>
                </div>

                <button class="btn btn-primary btn-sm" style="font-size: 0.78rem; width: 100%; display: flex; align-items: center; justify-content: center; gap: 6px;" onclick="LoginPage.syncBizCredential('${safeName}', '${safeFakeid}', '${profileUrl}')" title="复制该公众号专属主页链接并提示发送至微信打开">
                    🔄 刷新该公众号凭证
                </button>
            </div>
        `;
    },

    async manualSync() {
        if (this._savedAccountsCount === 0) {
            Toast.info('💡 您尚未在公众号管理中添加任何公众号，正在为您跳转到添加页面...');
            Router.navigate('accounts');
            return;
        }
        Toast.info('🚀 正在唤起微信客户端打开聚合页批量同步凭证，请稍候（约 10~15 秒）...');
        const btn = document.getElementById('btn-manual-sync');
        if (btn) {
            btn.disabled = true;
            btn.innerText = '⏳ 正在同步中...';
        }
        try {
            const data = await API.accountPool.syncManual();
            if (data.success) {
                Toast.success('🎉 全部公众号主页凭证已 100% 自动同步就绪！');
            } else {
                Toast.warning('批量同步部分完成或超时，可前往微信确认');
            }
        } catch (e) {
            Toast.error('手动同步请求失败: ' + e.message);
        } finally {
            if (btn) {
                btn.disabled = false;
            }
            await this.loadAccounts();
        }
    },

    async toggleAutoRefresh(enabled) {
        try {
            const data = await API.accountPool.toggleAutoRefresh(enabled);
            const intervalMin = data.interval_minutes || 5;
            const label = document.getElementById('label-auto-refresh');
            if (data.enabled) {
                Toast.success(`✅ 已开启定时自动同步（每 ${intervalMin} 分钟巡检一次）`);
                if (label) label.innerText = `定时自动同步 (每${intervalMin}分钟)`;
            } else {
                Toast.info('⏸️ 已关闭定时自动同步');
                if (label) label.innerText = '定时自动同步 (已关闭)';
            }
        } catch (e) {
            Toast.error('切换定时同步开关失败: ' + e.message);
        }
    },

    async loadAutoRefreshConfig() {
        try {
            const cfg = await API.accountPool.getAutoRefreshConfig();
            const sw = document.getElementById('switch-auto-refresh');
            const label = document.getElementById('label-auto-refresh');
            if (sw && cfg && typeof cfg.enabled === 'boolean') {
                sw.checked = cfg.enabled;
            }
            if (label && cfg) {
                const intervalMin = cfg.interval_minutes || 5;
                label.innerText = cfg.enabled ? `定时自动同步 (每${intervalMin}分钟)` : '定时自动同步 (已关闭)';
            }
        } catch (e) { /* silent */ }
    },

    async syncAllBatch() {
        return this.manualSync();
    },

    async syncBizCredential(name, fakeid, profileUrl) {
        if (!name && !fakeid) return;

        let targetBiz = fakeid || '';
        const url = profileUrl || (targetBiz ? `https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=${encodeURIComponent(targetBiz)}#wechat_redirect` : '');

        if (!url) {
            Toast.warning('未获取到该公众号的主页链接');
            return;
        }

        // 1. 复制链接到系统剪贴板
        let copied = false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(url);
                copied = true;
            }
        } catch (e) { /* fallback */ }

        if (!copied) {
            try {
                const ta = document.createElement('textarea');
                ta.value = url;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                copied = document.execCommand('copy');
                document.body.removeChild(ta);
            } catch (e) { /* ignore */ }
        }

        // 2. 检查同步助手代理状态以提供更贴心的提示
        let proxyRunning = false;
        try {
            const st = await API.channels.getProxyStatus();
            proxyRunning = !!st.proxy_running;
        } catch (e) { /* silent */ }

        const proxyNotice = !proxyRunning 
            ? `<div style="margin-top: 6px; padding: 4px 8px; background: rgba(255,149,0,0.12); border-radius: 6px; color: #d97706; font-size: 0.78rem;">⚠️ 提示：请确保顶部「启动同步助手」已开启，方可自动截获凭证！</div>` 
            : '';

        // 3. 弹出优化后的精美 Toast 提示
        Toast.show(
            `已复制【<strong>${this._esc(name)}</strong>】专属主页链接！<br>` +
            `<div style="margin: 6px 0; padding: 6px 8px; background: rgba(0,0,0,0.04); border-radius: 6px; font-family: monospace; font-size: 0.74rem; color: var(--text-secondary); word-break: break-all; user-select: all;">${url}</div>` +
            `<div style="color: var(--success, #07c160); font-weight: 600; margin-top: 4px;">👉 请发送到微信<strong>「文件传输助手」</strong>并点击打开，系统会自动同步凭证！</div>` +
            proxyNotice,
            'success',
            6000,
            '📋 公众号链接已复制'
        );
    },

    // ── 代理服务与证书管理 (与视频号登录页面 channels_login.js 保持一致性) ─────────────
    async checkProxyStatus() {
        try {
            const status = await API.channels.getProxyStatus();
            this.renderProxyUI(status);
        } catch (err) {
            console.error('Failed to get proxy status:', err);
        }
    },

    renderProxyUI(status) {
        const proxyBadge = document.getElementById('pool-proxy-status-badge');
        const certBadge = document.getElementById('pool-cert-status-badge');
        const toggleBtn = document.getElementById('pool-btn-toggle-proxy');
        const installBtn = document.getElementById('pool-btn-install-cert');
        const uninstallBtn = document.getElementById('pool-btn-uninstall-cert');

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

        if (certBadge && installBtn && uninstallBtn) {
            if (status.cert_installed) {
                certBadge.innerHTML = 'CA 证书: <strong style="color: #07c160;">已信任</strong>';
                installBtn.style.display = 'none';
                uninstallBtn.style.display = 'inline-flex';
            } else {
                certBadge.innerHTML = 'CA 证书: <strong style="color: #ff9500;">未信任</strong>';
                installBtn.style.display = 'inline-flex';
                uninstallBtn.style.display = 'none';
            }
        }
    },

    async toggleProxy() {
        const toggleBtn = document.getElementById('pool-btn-toggle-proxy');
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
        const installBtn = document.getElementById('pool-btn-install-cert');
        if (installBtn) installBtn.disabled = true;

        try {
            Toast.info('正在生成并安装证书，请在系统弹窗中确认信任...');
            await API.channels.installCert();
            Toast.success('证书安装成功！');
            await this.checkProxyStatus();
        } catch (err) {
            Toast.error('安装证书失败: ' + err.message);
        } finally {
            if (installBtn) installBtn.disabled = false;
        }
    },

    async uninstallCert() {
        const uninstallBtn = document.getElementById('pool-btn-uninstall-cert');
        if (uninstallBtn) uninstallBtn.disabled = true;

        try {
            await API.channels.uninstallCert();
            Toast.success('证书卸载成功！');
            await this.checkProxyStatus();
        } catch (err) {
            Toast.error('卸载证书失败: ' + err.message);
        } finally {
            if (uninstallBtn) uninstallBtn.disabled = false;
        }
    },

    _renderCard(acc) {
        const statusColor = this.statusColor(acc.status);
        const statusText = this.statusLabel(acc.status);
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
                    <div>公众号专属凭证: <strong>${acc.biz_count || 0}</strong> 个${acc.biz_count ? '' : '（打开任一公众号文章后自动建立）'}</div>
                    <div>
                        凭证状态: <strong style="color: var(--success);">长期有效</strong>
                    </div>
                    ${acc.save_time ? `<div style="grid-column: span 2; color: var(--text-muted); font-size: 0.76rem;">(${this.formatDate(acc.save_time)} 更新)</div>` : ''}
                </div>

                ${extraInfo}

                <!-- 操作按钮 -->
                <div style="display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap;">
                    ${isKicked ? `
                        <button class="btn btn-primary btn-sm" onclick="LoginPage.startLogin()" style="flex: 1; font-size: 0.8rem;">重新登录</button>
                    ` : ''}
                    <button class="btn btn-danger btn-sm" onclick="LoginPage.removeAccount('${acc.id}')" style="font-size: 0.8rem; ${isKicked ? '' : 'margin-left: auto;'}">
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

    async removeAccount(id) {
        const acc = (this._accounts || []).find(a => a.id === id);
        const name = acc ? (acc.nickname || '该账号') : '该账号';
        Modal.confirm('删除账号', `确定要从账号池中删除「${name}」吗？`, async () => {
            try {
                await API.accountPool.remove(id);
                Toast.success('已删除');
                await this.loadAccounts();
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
