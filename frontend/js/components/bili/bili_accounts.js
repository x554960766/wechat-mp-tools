/**
 * B站UP主管理组件
 */
const BiliAccountsPage = {
    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">B站UP主管理</h2>
                    <p class="page-description">收藏并管理哔哩哔哩 UP 主，快速查看其空间投稿视频，支持勾选进行批量下载。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 24px; padding: 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 12px; font-size: 1.1rem; color: var(--text-primary);">添加订阅UP主</h3>
                <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                    <input type="text" id="bili-up-url-input" class="form-control" placeholder="请输入主页链接或MID，如：https://space.bilibili.com/2  或直接输入 MID" style="flex: 1; min-width: 280px; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary);" />
                    <button class="btn btn-primary" id="btn-bili-parse-user" onclick="BiliAccountsPage.parseUser()">添加UP主</button>
                </div>
            </div>

            <div id="bili-up-preview-container" style="margin-bottom: 24px; display: none;"></div>

            <div id="bili-accounts-list-container" class="animate-fade-in">
                <div class="spinner" style="margin: 40px auto;"></div>
            </div>
        `;
    },

    async init() {
        await this.loadAccounts();
    },

    async loadAccounts() {
        const container = document.getElementById('bili-accounts-list-container');
        if (!container) return;

        try {
            const data = await API.bili.listAccounts();
            const accounts = data.accounts || [];
            if (accounts.length === 0) {
                container.innerHTML = `
                    <div class="empty-state" style="text-align: center; padding: 60px 24px;">
                        <div style="font-size: 3rem; margin-bottom: 16px; opacity: 0.4;">📺</div>
                        <h3 style="color: var(--text-primary); margin-bottom: 8px;">您还没有添加任何订阅 UP 主</h3>
                        <p style="color: var(--text-muted); margin-bottom: 24px;">在上方输入 UP 主的空间链接或 MID，快速追踪和获取视频吧！</p>
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
            container.innerHTML = `<div style="text-align: center; color: var(--error); padding: 40px;">加载UP主列表失败: ${err.message}</div>`;
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
                        ? `<img src="${acc.avatar}" alt="" style="width: 50px; height: 50px; border-radius: 50%; object-fit: cover; border: 2px solid white; box-shadow: var(--shadow-sm);" referrerpolicy="no-referrer"
                             onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                           <div style="display: none; width: 50px; height: 50px; border-radius: 50%; background: var(--primary); color: white; align-items: center; justify-content: center; font-size: 1.3rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                        : `<div style="width: 50px; height: 50px; border-radius: 50%; background: var(--primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 1.3rem; font-weight: 700; flex-shrink: 0;">${initial}</div>`
                    }
                    <div style="flex: 1; min-width: 0;">
                        <h4 style="margin: 0 0 4px; font-size: 1.05rem; font-weight: 700; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${this._esc(acc.nickname)}">${this._esc(acc.nickname)}</h4>
                        <p style="margin: 0 0 2px; font-size: 0.8rem; color: var(--text-muted);">MID: ${acc.mid || '无'}</p>
                    </div>
                </div>

                <div style="font-size: 0.82rem; color: var(--text-secondary); line-height: 1.4; margin-bottom: 16px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; height: 36px; word-break: break-all;" title="${this._esc(acc.desc)}">
                    ${this._esc(acc.desc || '暂无个人简介')}
                </div>

                <div style="display: flex; gap: 8px; border-top: 1px solid var(--border-color); padding-top: 12px; margin-top: auto;">
                    <button class="btn btn-primary btn-sm" style="flex: 1; font-size: 0.8rem;" onclick="Router.navigate('bili_videos', {mid: '${acc.mid}'})">📺 投稿视频</button>
                    <button class="btn btn-danger btn-sm" style="font-size: 0.8rem;" onclick="BiliAccountsPage.removeAccount('${acc.mid}', '${this._esc(acc.nickname)}')">删除</button>
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
        const urlInput = document.getElementById('bili-up-url-input');
        const parseBtn = document.getElementById('btn-bili-parse-user');
        const previewContainer = document.getElementById('bili-up-preview-container');
        
        const url = urlInput.value.trim();
        if (!url) {
            Toast.error('请输入UP主主页链接或MID');
            return;
        }

        if (parseBtn) {
            parseBtn.disabled = true;
            parseBtn.innerHTML = '<div class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></div> 解析中...';
        }

        try {
            const data = await API.bili.parseUser(url);
            
            previewContainer.style.display = 'block';
            previewContainer.innerHTML = `
                <div style="background: var(--bg-card); border: 1px solid var(--primary); border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 16px; box-shadow: var(--shadow-sm);">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <h4 style="margin: 0; color: var(--primary); font-size: 1rem; font-weight: 700;">🔍 预览解析UP主信息</h4>
                        <button class="btn btn-secondary btn-sm" onclick="BiliAccountsPage.closePreview()" style="padding: 2px 8px; font-size: 0.75rem;">关闭</button>
                    </div>
                    
                    <div style="display: flex; gap: 16px; align-items: center;">
                        ${data.avatar
                            ? `<img src="${data.avatar}" alt="" style="width: 60px; height: 60px; border-radius: 50%; object-fit: cover; border: 2px solid white; box-shadow: var(--shadow-sm);" referrerpolicy="no-referrer"
                                 onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                               <div style="display: none; width: 60px; height: 60px; border-radius: 50%; background: var(--primary); color: white; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700; flex-shrink: 0;">${data.nickname.charAt(0)}</div>`
                            : `<div style="width: 60px; height: 60px; border-radius: 50%; background: var(--primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700; flex-shrink: 0;">${data.nickname.charAt(0)}</div>`
                        }
                        <div style="flex: 1; min-width: 0;">
                            <h4 style="margin: 0 0 4px; font-size: 1.1rem; color: var(--text-primary); font-weight: 700;">${this._esc(data.nickname)}</h4>
                            <p style="margin: 0; font-size: 0.8rem; color: var(--text-muted);">MID: ${data.mid}</p>
                            <p style="margin: 4px 0 0; font-size: 0.82rem; color: var(--text-secondary); line-height: 1.4; word-break: break-all;">${this._esc(data.desc || '暂无简介')}</p>
                        </div>
                    </div>
                    
                    <div style="display: flex; justify-content: flex-end; gap: 8px;">
                        <button class="btn btn-secondary btn-sm" onclick="BiliAccountsPage.closePreview()">取消</button>
                        <button class="btn btn-primary btn-sm" onclick="BiliAccountsPage.addAccount(${JSON.stringify(data).replace(/"/g, '&quot;')})">收藏并追踪</button>
                    </div>
                </div>
            `;
        } catch (err) {
            Toast.error('解析失败: ' + err.message);
        } finally {
            if (parseBtn) {
                parseBtn.disabled = false;
                parseBtn.innerHTML = '添加UP主';
            }
        }
    },

    closePreview() {
        const previewContainer = document.getElementById('bili-up-preview-container');
        if (previewContainer) {
            previewContainer.innerHTML = '';
            previewContainer.style.display = 'none';
        }
    },

    async addAccount(user) {
        try {
            await API.bili.addAccount(user);
            Toast.success('已添加关注UP主');
            this.closePreview();
            const urlInput = document.getElementById('bili-up-url-input');
            if (urlInput) urlInput.value = '';
            await this.loadAccounts();
        } catch (err) {
            Toast.error('添加失败: ' + err.message);
        }
    },

    removeAccount(mid, name) {
        Modal.confirm('取消关注', `确认要取消对 UP 主 「${name}」 的关注吗？`, async () => {
            try {
                await API.bili.removeAccount(mid);
                Toast.success('已取消关注');
                await this.loadAccounts();
            } catch (err) {
                Toast.error('取消关注失败: ' + err.message);
            }
        });
    }
};
