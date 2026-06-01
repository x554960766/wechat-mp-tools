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
};
