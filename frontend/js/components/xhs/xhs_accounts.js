/**
 * 小红书博主管理组件
 */
const XhsAccountsPage = {
    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">小红书博主管理</h2>
                    <p class="page-description">收藏管理小红书博主，快速查看并批量下载博主的主页笔记。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 24px; padding: 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 12px; font-size: 1.1rem; color: var(--text-primary);">添加博主</h3>
                <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                    <input type="text" id="xhs-blogger-url-input" class="form-control" placeholder="请输入小红书博主主页链接，如：https://www.xiaohongshu.com/user/profile/..." style="flex: 1; min-width: 280px; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary);" />
                    <button class="btn btn-primary" id="btn-xhs-parse-user" onclick="XhsAccountsPage.parseUser()">添加博主</button>
                </div>
            </div>

            <div id="xhs-blogger-preview-container" style="margin-bottom: 24px; display: none;"></div>

            <div id="xhs-accounts-list-container" class="animate-fade-in">
                <div class="spinner" style="margin: 40px auto;"></div>
            </div>
        `;
    },

    async init() {
        await this.loadAccounts();
    },

    async loadAccounts() {
        const container = document.getElementById('xhs-accounts-list-container');
        if (!container) return;

        try {
            const data = await API.xhs.listAccounts();
            const accounts = data.accounts || [];
            if (accounts.length === 0) {
                container.innerHTML = `
                    <div class="empty-state" style="text-align: center; padding: 60px 24px;">
                        <div style="font-size: 3rem; margin-bottom: 16px; opacity: 0.4;">👥</div>
                        <h3 style="color: var(--text-primary); margin-bottom: 8px;">未收藏任何博主</h3>
                        <p style="color: var(--text-muted); margin-bottom: 24px;">输入博主主页链接添加您的第一个关注博主吧！</p>
                    </div>
                `;
                return;
            }

            container.innerHTML = `
                <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px;">
                    ${accounts.map(acc => this._renderCard(acc)).join('')}
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<div style="text-align: center; color: var(--error); padding: 40px;">加载博主列表失败: ${err.message}</div>`;
        }
    },

    _renderCard(acc) {
        const initial = (acc.nickname || '?').charAt(0);
        return `
            <div style="
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 12px;
                padding: 20px;
                display: flex;
                flex-direction: column;
                justify-content: space-between;
                min-height: 180px;
                transition: box-shadow 0.2s, transform 0.2s;
            " onmouseenter="this.style.boxShadow='var(--shadow-md)';this.style.transform='translateY(-2px)'"
              onmouseleave="this.style.boxShadow='none';this.style.transform='none'">
                
                <div style="display: flex; gap: 12px; align-items: flex-start; margin-bottom: 12px;">
                    ${acc.avatar
                        ? `<img src="${acc.avatar}" alt="" style="width: 50px; height: 50px; border-radius: 50%; object-fit: cover; border: 2px solid white; box-shadow: var(--shadow-sm);"
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                           <div style="display: none; width: 50px; height: 50px; border-radius: 50%; background: var(--primary); color: white; align-items: center; justify-content: center; font-size: 1.3rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                        : `<div style="width: 50px; height: 50px; border-radius: 50%; background: var(--primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 1.3rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                    }
                    <div style="flex: 1; min-width: 0;">
                        <h4 style="margin: 0 0 4px; font-size: 1.05rem; font-weight: 700; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${this._esc(acc.nickname)}</h4>
                        <p style="margin: 0 0 2px; font-size: 0.8rem; color: var(--text-muted);">小红书号: ${acc.red_id || '无'}</p>
                        <p style="margin: 0; font-size: 0.8rem; color: var(--text-muted);">粉丝数: <strong style="color: var(--text-primary);">${acc.fans || '0'}</strong></p>
                    </div>
                </div>

                <div style="font-size: 0.82rem; color: var(--text-secondary); line-height: 1.4; margin-bottom: 16px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; height: 36px; word-break: break-all;">
                    ${this._esc(acc.desc || '暂无简介')}
                </div>

                <div style="display: flex; gap: 8px; border-top: 1px solid var(--border-color); padding-top: 12px; margin-top: auto;">
                    <button class="btn btn-primary btn-sm" style="flex: 1; font-size: 0.8rem;" onclick="Router.navigate('xhs_notes', {user_id: '${acc.user_id}'})">📖 查看笔记</button>
                    <button class="btn btn-danger btn-sm" style="font-size: 0.8rem;" onclick="XhsAccountsPage.removeAccount('${acc.user_id}', '${this._esc(acc.nickname)}')">删除</button>
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

    async parseUser() {
        const urlInput = document.getElementById('xhs-blogger-url-input');
        const parseBtn = document.getElementById('btn-xhs-parse-user');
        const previewContainer = document.getElementById('xhs-blogger-preview-container');
        
        const url = urlInput.value.trim();
        if (!url) {
            Toast.error('请输入博主主页链接');
            return;
        }

        if (parseBtn) {
            parseBtn.disabled = true;
            parseBtn.innerHTML = '<div class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></div> 解析中...';
        }

        try {
            const data = await API.xhs.parseUser(url);
            const user = data.user;
            if (!user) {
                throw new Error('未能在返回数据中解析到博主信息');
            }

            previewContainer.style.display = 'block';
            previewContainer.innerHTML = `
                <div style="background: var(--bg-card); border: 1px solid var(--primary); border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 16px; box-shadow: var(--shadow-sm);">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <h4 style="margin: 0; color: var(--primary); font-size: 1rem; font-weight: 700;">🔍 预览解析博主信息</h4>
                        <button class="btn btn-secondary btn-sm" onclick="XhsAccountsPage.closePreview()" style="padding: 2px 8px; font-size: 0.75rem;">关闭</button>
                    </div>
                    
                    <div style="display: flex; gap: 16px; align-items: center;">
                        ${user.avatar
                            ? `<img src="${user.avatar}" alt="" style="width: 60px; height: 60px; border-radius: 50%; object-fit: cover; border: 2px solid white; box-shadow: var(--shadow-sm);"
                                 onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                               <div style="display: none; width: 60px; height: 60px; border-radius: 50%; background: var(--primary); color: white; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700; flex-shrink: 0;">${user.nickname.charAt(0)}</div>`
                            : `<div style="width: 60px; height: 60px; border-radius: 50%; background: var(--primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700; flex-shrink: 0;">${user.nickname.charAt(0)}</div>`
                        }
                        <div style="flex: 1; min-width: 0;">
                            <h4 style="margin: 0 0 4px; font-size: 1.1rem; color: var(--text-primary); font-weight: 700;">${this._esc(user.nickname)}</h4>
                            <p style="margin: 0 0 2px; font-size: 0.85rem; color: var(--text-muted);">小红书号: ${user.red_id || '无'} | 粉丝数: <strong style="color: var(--text-primary);">${user.fans || '0'}</strong></p>
                            <p style="margin: 0; font-size: 0.85rem; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${this._esc(user.desc || '暂无简介')}</p>
                        </div>
                    </div>
                    
                    <div style="display: flex; gap: 12px; justify-content: flex-end;">
                        <button class="btn btn-secondary" onclick="XhsAccountsPage.closePreview()">取消</button>
                        <button class="btn btn-primary" onclick="XhsAccountsPage.addAccount(${JSON.stringify(user).replace(/"/g, '&quot;')})">确认收藏博主</button>
                    </div>
                </div>
            `;
        } catch (err) {
            Toast.error('解析博主失败: ' + err.message);
        } finally {
            if (parseBtn) {
                parseBtn.disabled = false;
                parseBtn.innerHTML = '添加博主';
            }
        }
    },

    closePreview() {
        const previewContainer = document.getElementById('xhs-blogger-preview-container');
        if (previewContainer) {
            previewContainer.style.display = 'none';
            previewContainer.innerHTML = '';
        }
    },

    async addAccount(user) {
        try {
            await API.xhs.addAccount(user);
            Toast.success('博主收藏成功');
            this.closePreview();
            const urlInput = document.getElementById('xhs-blogger-url-input');
            if (urlInput) urlInput.value = '';
            await this.loadAccounts();
        } catch (err) {
            Toast.error('收藏失败: ' + err.message);
        }
    },

    removeAccount(userId, nickname) {
        Modal.confirm('取消收藏博主', `确定要取消收藏博主「${nickname}」吗？取消后历史下载文件不会被删除。`, async () => {
            try {
                await API.xhs.removeAccount(userId);
                Toast.success('取消收藏成功');
                await this.loadAccounts();
            } catch (err) {
                Toast.error('操作失败: ' + err.message);
            }
        });
    }
};
