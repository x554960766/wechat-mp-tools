/**
 * 公众号管理页面组件
 */
const AccountsPage = {
    accounts: [],

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">公众号管理</h2>
                <p class="page-description">搜索、收藏和管理您关注的微信公众号</p>
            </div>

            <!-- 搜索区域 -->
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="card-header">
                    <h3 class="card-title">🔍 搜索公众号</h3>
                </div>
                <div style="display: flex; gap: var(--spacing-sm);">
                    <div class="search-box" style="flex: 1; max-width: none;">
                        <svg class="search-icon" viewBox="0 0 24 24" fill="none">
                            <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/>
                            <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                        </svg>
                        <input type="text" class="form-input" id="search-account-input"
                               placeholder="输入公众号名称搜索..."
                               onkeydown="if(event.key==='Enter') AccountsPage.search()">
                    </div>
                    <button class="btn btn-primary" onclick="AccountsPage.search()" id="btn-search-account">
                        搜索
                    </button>
                </div>
                <div id="search-results" class="search-results"></div>
            </div>

            <!-- 已收藏列表 -->
            <div class="card">
                <div class="card-header">
                    <h3 class="card-title">⭐ 已收藏的公众号</h3>
                    <span class="badge badge-info" id="accounts-count">0 个</span>
                </div>
                <div id="accounts-list" class="animate-stagger">
                    <div class="loading-screen" style="min-height: 200px;">
                        <div class="spinner"></div>
                        <p>加载中...</p>
                    </div>
                </div>
            </div>
        `;
    },

    async init() {
        await this.loadAccounts();
    },

    async loadAccounts() {
        try {
            const data = await API.accounts.list();
            this.accounts = data.accounts || [];
            this.renderAccounts();
        } catch (err) {
            document.getElementById('accounts-list').innerHTML =
                '<div class="empty-state"><p class="empty-state-desc">加载失败，请检查网络连接</p></div>';
        }
    },

    renderAccounts() {
        const container = document.getElementById('accounts-list');
        const countBadge = document.getElementById('accounts-count');
        if (!container) return;

        if (countBadge) countBadge.textContent = `${this.accounts.length} 个`;

        if (this.accounts.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">
                        <svg viewBox="0 0 24 24" fill="none" width="80" height="80">
                            <path d="M17 21V19C17 16.79 15.21 15 13 15H5C2.79 15 1 16.79 1 19V21" stroke="currentColor" stroke-width="1.5"/>
                            <circle cx="9" cy="7" r="4" stroke="currentColor" stroke-width="1.5"/>
                            <path d="M23 21V19C23 17.34 21.94 15.91 20.44 15.34" stroke="currentColor" stroke-width="1.5"/>
                            <path d="M16.44 3.34C17.94 3.91 19 5.34 19 7C19 8.66 17.94 10.09 16.44 10.66" stroke="currentColor" stroke-width="1.5"/>
                        </svg>
                    </div>
                    <h3 class="empty-state-title">还没有收藏的公众号</h3>
                    <p class="empty-state-desc">在上方搜索框中输入公众号名称，搜索并添加到收藏</p>
                </div>
            `;
            return;
        }

        container.innerHTML = this.accounts.map(acc => `
            <div class="account-card" data-fakeid="${acc.fakeid}">
                ${acc.round_head_img
                    ? `<img class="account-avatar" src="${acc.round_head_img}" alt="${acc.nickname}" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                       <div class="account-avatar-placeholder" style="display: none;">${acc.nickname.charAt(0)}</div>`
                    : `<div class="account-avatar-placeholder">${acc.nickname.charAt(0)}</div>`
                }
                <div class="account-info">
                    <div class="account-name">${acc.nickname}</div>
                    <div class="account-id">${acc.alias ? '@' + acc.alias : ''} · ${acc.fakeid.substring(0, 10)}...</div>
                    ${acc.signature ? `<div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${acc.signature}</div>` : ''}
                </div>
                <div class="account-actions">
                    <button class="btn btn-sm btn-primary" onclick="event.stopPropagation(); Router.navigate('articles', { fakeid: '${acc.fakeid}', name: '${acc.nickname}' })">
                        📰 文章
                    </button>
                    <button class="btn btn-sm" style="background: rgba(255, 152, 0, 0.12); color: #ff9800; border: 1px solid rgba(255, 152, 0, 0.25);" onclick="event.stopPropagation(); AccountsPage.showRssModal('${acc.fakeid}', '${acc.nickname.replace(/'/g, "\\'")}')">
                        📡 RSS
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="event.stopPropagation(); AccountsPage.removeAccount('${acc.fakeid}', '${acc.nickname}')">
                        🗑
                    </button>
                </div>
            </div>
        `).join('');
    },

    async search() {
        const input = document.getElementById('search-account-input');
        const keyword = input?.value.trim();
        if (!keyword) {
            Toast.warning('请输入搜索关键字');
            return;
        }

        const btn = document.getElementById('btn-search-account');
        const results = document.getElementById('search-results');

        btn.disabled = true;
        btn.textContent = '搜索中...';
        results.innerHTML = '<div style="padding: 16px; text-align: center;"><div class="spinner" style="margin: 0 auto;"></div></div>';

        try {
            const data = await API.accounts.search(keyword);
            const items = data.results || [];

            if (items.length === 0) {
                results.innerHTML = `
                    <div style="padding: 16px; text-align: center; color: var(--text-muted);">
                        未找到匹配的公众号
                    </div>
                `;
                return;
            }

            // 检查哪些已收藏
            const savedIds = new Set(this.accounts.map(a => a.fakeid));

            results.innerHTML = items.map(item => `
                <div class="account-card">
                    ${item.round_head_img
                        ? `<img class="account-avatar" src="${item.round_head_img}" alt="${item.nickname}" onerror="this.outerHTML='<div class=\\'account-avatar-placeholder\\'>${item.nickname.charAt(0)}</div>'">`
                        : `<div class="account-avatar-placeholder">${item.nickname.charAt(0)}</div>`
                    }
                    <div class="account-info">
                        <div class="account-name">${item.nickname}</div>
                        <div class="account-id">${item.alias ? '@' + item.alias : ''} · ${item.fakeid.substring(0, 10)}...</div>
                        ${item.signature ? `<div style="font-size: 0.78rem; color: var(--text-muted); margin-top: 2px;">${item.signature}</div>` : ''}
                    </div>
                    <div class="account-actions">
                        ${savedIds.has(item.fakeid)
                            ? '<span class="badge badge-success">已收藏</span>'
                            : `<button class="btn btn-sm btn-success" onclick="AccountsPage.addAccount(${JSON.stringify(item).replace(/"/g, '&quot;')})">
                                + 收藏
                               </button>`
                        }
                    </div>
                </div>
            `).join('');

        } catch (err) {
            results.innerHTML = `
                <div style="padding: 16px; text-align: center; color: var(--error);">
                    搜索失败: ${err.message}
                </div>
            `;
        } finally {
            btn.disabled = false;
            btn.textContent = '搜索';
        }
    },

    async addAccount(accountData) {
        try {
            await API.accounts.add(accountData);
            Toast.success(`已收藏: ${accountData.nickname}`);
            await this.loadAccounts();
            // 刷新搜索结果
            const input = document.getElementById('search-account-input');
            if (input?.value.trim()) {
                await this.search();
            }
        } catch (err) {
            // error already shown by API
        }
    },

    async removeAccount(fakeid, nickname) {
        Modal.confirm('删除确认', `确定要从收藏中移除「${nickname}」吗？`, async () => {
            try {
                await API.accounts.remove(fakeid);
                Toast.success(`已移除: ${nickname}`);
                await AccountsPage.loadAccounts();
            } catch (err) {
                // error shown by API
            }
        });
    },

    async showRssModal(fakeid, nickname) {
        const rssUrl = `${window.location.origin}/api/articles/rss/${encodeURIComponent(nickname)}`;

        // Remove existing modal if any
        const existing = document.getElementById('rss-modal-overlay');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.id = 'rss-modal-overlay';
        overlay.className = 'modal-overlay active';
        document.body.appendChild(overlay);

        // Fetch current subscription status
        let sub = null;
        try {
            const data = await API.accounts.rssSubscriptions();
            sub = (data.subscriptions || []).find(s => s.fakeid === fakeid);
        } catch (err) {
            console.error("加载订阅状态失败", err);
        }

        let isSubscribed = sub ? sub.enabled : false;
        let interval = sub ? sub.interval_minutes : 60;

        const updateModalBody = () => {
            const statusSection = isSubscribed ? `
                <div style="margin-top: var(--spacing-md); padding: var(--spacing-sm) var(--spacing-md); background: rgba(255, 152, 0, 0.05); border-radius: var(--radius-md); border: 1px solid var(--border-color); font-size: 0.82rem;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">订阅状态：</span>
                        <span style="font-weight: 600; color: ${sub?.last_error ? 'var(--error)' : 'var(--success, #4caf50)'};">
                            ${sub?.last_error ? `⚠️ 异常 (${sub.last_error})` : '🟢 运行中'}
                        </span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">抓取间隔：</span>
                        <span>每 ${interval >= 60 ? (interval / 60) + ' 小时' : interval + ' 分钟'}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">上次抓取：</span>
                        <span>${sub?.last_fetch_time ? new Date(sub.last_fetch_time * 1000).toLocaleString() : '暂无'}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">上次新增：</span>
                        <span>${sub?.last_fetch_count !== undefined ? sub.last_fetch_count + ' 篇' : '暂无'}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">上传状态：</span>
                        <span style="font-weight: 600; color: ${sub?.last_upload_error ? 'var(--badge-warning-color)' : 'var(--success, #4caf50)'};">
                            ${this.formatRssUploadStatus(sub)}
                        </span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                        <span style="color: var(--text-muted);">上次上传：</span>
                        <span>${sub?.last_upload_time ? new Date(sub.last_upload_time * 1000).toLocaleString() : '暂无'}</span>
                    </div>
                    <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
                        <button class="btn btn-secondary btn-sm" id="rss-refresh-status-btn" style="padding: 3px 10px; font-size: 0.75rem;">刷新状态</button>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: var(--text-muted);">累计抓取：</span>
                        <span>${sub?.total_articles || 0} 篇</span>
                    </div>
                </div>
            ` : `
                <div style="margin-top: var(--spacing-md); padding: var(--spacing-sm) var(--spacing-md); background: var(--bg-glass); border-radius: var(--radius-md); border: 1px solid var(--border-color); font-size: 0.82rem;">
                    <p style="color: var(--text-muted); margin: 0; text-align: center;">
                        💡 开启自动订阅后，系统会定时抓取该公众号最新文章并更新到 RSS。
                    </p>
                </div>
            `;

            overlay.innerHTML = `
                <div class="modal-dialog" style="max-width: 560px;">
                    <div class="modal-header">
                        <h3 class="modal-title">📡 RSS 订阅 — ${nickname}</h3>
                        <button class="modal-close" id="rss-modal-close-btn">&times;</button>
                    </div>
                    <div class="modal-body">
                        <!-- Switch toggle -->
                        <div class="switch-group" style="margin-bottom: var(--spacing-md);">
                            <div class="switch-label">
                                <span class="label-title">自动订阅抓取</span>
                                <span class="label-desc">开启后系统后台定时抓取该公众号最新文章</span>
                            </div>
                            <label class="switch">
                                <input type="checkbox" id="rss-enable-checkbox" ${isSubscribed ? 'checked' : ''}>
                                <span class="switch-slider"></span>
                            </label>
                        </div>

                        <!-- Interval select (collapsible if subscribed) -->
                        <div id="rss-interval-container" style="display: ${isSubscribed ? 'block' : 'none'}; margin-bottom: var(--spacing-md);">
                            <label style="display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: var(--text-primary);">
                                抓取时间间隔
                            </label>
                            <select class="form-input" id="rss-interval-select" style="width: 100%;">
                                <option value="15" ${interval === 15 ? 'selected' : ''}>每 15 分钟</option>
                                <option value="30" ${interval === 30 ? 'selected' : ''}>每 30 分钟</option>
                                <option value="60" ${interval === 60 ? 'selected' : ''}>每 1 小时 (推荐)</option>
                                <option value="120" ${interval === 120 ? 'selected' : ''}>每 2 小时</option>
                                <option value="360" ${interval === 360 ? 'selected' : ''}>每 6 小时</option>
                                <option value="720" ${interval === 720 ? 'selected' : ''}>每 12 小时</option>
                                <option value="1440" ${interval === 1440 ? 'selected' : ''}>每 24 小时</option>
                            </select>
                        </div>

                        <!-- Status Section -->
                        ${statusSection}

                        <hr style="border: 0; border-top: 1px solid var(--border-color); margin: var(--spacing-lg) 0;">

                        <!-- RSS Feed URL -->
                        <label style="display: block; font-size: 0.85rem; font-weight: 500; margin-bottom: 6px; color: var(--text-primary);">
                            RSS Feed 地址
                        </label>
                        <div style="display: flex; gap: var(--spacing-sm); align-items: center;">
                            <input type="text" class="form-input" value="${rssUrl}" readonly id="rss-url-input"
                                   style="flex: 1; font-size: 0.82rem; font-family: monospace; cursor: text;"
                                   onclick="this.select()">
                            <button class="btn btn-primary" id="rss-copy-btn" style="white-space: nowrap;">
                                📋 复制
                            </button>
                        </div>
                    </div>
                    <div class="modal-footer" style="margin-top: var(--spacing-lg);">
                        <a href="${rssUrl}" target="_blank" class="btn" style="font-size: 0.85rem;">🔗 在浏览器中预览</a>
                        <button class="btn" id="rss-modal-close-btn2">关闭</button>
                    </div>
                </div>
            `;

            // Bind close buttons
            overlay.querySelector('#rss-modal-close-btn').addEventListener('click', () => overlay.remove());
            overlay.querySelector('#rss-modal-close-btn2').addEventListener('click', () => overlay.remove());

            // Bind copy button
            overlay.querySelector('#rss-copy-btn').addEventListener('click', () => AccountsPage.copyRssUrl());

            // Bind switch change
            const checkbox = overlay.querySelector('#rss-enable-checkbox');
            const intervalContainer = overlay.querySelector('#rss-interval-container');
            const intervalSelect = overlay.querySelector('#rss-interval-select');
            const refreshStatusBtn = overlay.querySelector('#rss-refresh-status-btn');

            if (refreshStatusBtn) {
                refreshStatusBtn.addEventListener('click', async () => {
                    const subsData = await API.accounts.rssSubscriptions();
                    sub = (subsData.subscriptions || []).find(s => s.fakeid === fakeid);
                    isSubscribed = sub ? sub.enabled : false;
                    interval = sub ? sub.interval_minutes : interval;
                    updateModalBody();
                });
            }

            checkbox.addEventListener('change', async (e) => {
                const checked = e.target.checked;
                if (checked) {
                    intervalContainer.style.display = 'block';
                    try {
                        const selectedInterval = parseInt(intervalSelect.value);
                        Toast.info('正在开启自动订阅...');
                        const res = await API.accounts.rssSubscribe(fakeid, selectedInterval);
                        sub = res.subscription;
                        Toast.success(res.message || `已开启 RSS 自动抓取：${nickname}`);
                        // Re-fetch subscriptions to refresh detailed info
                        const subsData = await API.accounts.rssSubscriptions();
                        sub = (subsData.subscriptions || []).find(s => s.fakeid === fakeid);
                        isSubscribed = true;
                        interval = selectedInterval;
                        updateModalBody();
                    } catch (err) {
                        e.target.checked = false;
                        intervalContainer.style.display = 'none';
                    }
                } else {
                    intervalContainer.style.display = 'none';
                    try {
                        Toast.info('正在关闭自动订阅...');
                        await API.accounts.rssUnsubscribe(fakeid);
                        sub = null;
                        isSubscribed = false;
                        Toast.success(`已取消 RSS 自动抓取：${nickname}`);
                        updateModalBody();
                    } catch (err) {
                        e.target.checked = true;
                        intervalContainer.style.display = 'block';
                    }
                }
            });

            // Bind interval change
            intervalSelect.addEventListener('change', async (e) => {
                const selectedInterval = parseInt(e.target.value);
                try {
                    Toast.info('正在保存时间间隔...');
                    const res = await API.accounts.rssSubscribe(fakeid, selectedInterval);
                    sub = res.subscription;
                    interval = selectedInterval;
                    const intervalText = selectedInterval >= 60 ? (selectedInterval / 60) + ' 小时' : selectedInterval + ' 分钟';
                    const suffix = res.immediate_fetch === false ? '，当前不在采集时间段内' : '';
                    Toast.success(`订阅抓取间隔已更新为每 ${intervalText}${suffix}`);
                    updateModalBody();
                } catch (err) {
                    intervalSelect.value = interval;
                }
            });
        };

        updateModalBody();

        // Close on backdrop click
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });
    },

    async copyRssUrl() {
        const input = document.getElementById('rss-url-input');
        if (!input) return;
        try {
            await navigator.clipboard.writeText(input.value);
            const btn = document.getElementById('rss-copy-btn');
            if (btn) {
                btn.textContent = '✅ 已复制';
                setTimeout(() => { btn.textContent = '📋 复制'; }, 2000);
            }
            Toast.success('RSS 地址已复制到剪贴板');
        } catch {
            // Fallback for older browsers
            input.select();
            document.execCommand('copy');
            Toast.success('RSS 地址已复制');
        }
    },

    formatRssUploadStatus(sub) {
        if (!sub) return '暂无';
        const pending = sub.pending_upload_count || 0;
        if (sub.last_upload_disabled) {
            return pending > 0 ? `已关闭，待上传 ${pending} 篇` : '已关闭';
        }
        if (pending > 0) {
            return `待上传 ${pending} 篇`;
        }
        if (sub.last_upload_error) {
            return `异常：${sub.last_upload_error}`;
        }
        if (sub.last_upload_time) {
            return `成功 ${sub.last_upload_count || 0} 篇`;
        }
        return '暂无';
    },
};
