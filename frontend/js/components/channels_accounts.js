/**
 * 视频号作者管理页面组件
 */
const ChannelsAccountsPage = {
    favorites: [],
    resolvedAuthor: null,
    isParsing: false,

    render() {
        return `
            <div class="page-header animate-fade-in">
                <h2 class="page-title">视频号管理</h2>
                <p class="page-description">根据视频链接解析并收藏视频号作者，查看并同步创作者作品，支持批量下载视频。</p>
            </div>

            <!-- 同步助手 & 拦截代理设置 (证书注入 JS 方式) -->
            <div class="card animate-fade-in" style="margin-top: var(--spacing-lg); margin-bottom: var(--spacing-lg); border: 1px solid rgba(7, 193, 96, 0.15); background: linear-gradient(135deg, var(--bg-card) 0%, rgba(7, 193, 96, 0.03) 100%);">
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
                        <button class="btn btn-secondary" id="btn-install-cert" onclick="ChannelsAccountsPage.installCert()" style="font-size: 0.85rem; padding: 10px 16px; display: flex; align-items: center; gap: 6px;">
                            🛡️ 安装/信任证书
                        </button>
                        <a href="/api/channels/proxy/download-cert" class="btn btn-secondary" style="font-size: 0.85rem; padding: 10px 16px; text-decoration: none; display: flex; align-items: center; gap: 6px;" title="下载证书供手动安装">
                            📥 下载证书
                        </a>
                        <button class="btn btn-primary" id="btn-toggle-proxy" onclick="ChannelsAccountsPage.toggleProxy()" style="min-width: 140px; font-weight: 600; padding: 10px 20px;">
                            🚀 启动同步助手
                        </button>
                    </div>
                </div>
            </div>

            <!-- 解析与添加区域 -->
            <div class="card animate-fade-in" style="margin-top: var(--spacing-lg); margin-bottom: var(--spacing-lg);">
                <div class="card-header" style="display: flex; justify-content: space-between; align-items: center; padding-bottom: var(--spacing-sm); border-bottom: 1px solid rgba(0,0,0,0.05);">
                    <h3 class="card-title" style="margin: 0;">🔍 解析并收藏作者</h3>
                    <span style="font-size: 0.85rem; color: var(--text-muted);">通过粘贴作者任意视频的链接来提取作者信息</span>
                </div>
                
                <div class="form-group" style="margin-top: var(--spacing-md);">
                    <div style="display: flex; gap: var(--spacing-sm);">
                        <input type="text" id="channels-author-url-input" class="form-input" 
                               placeholder="粘贴作者任意视频的分享链接 (如 https://weixin.qq.com/sph/xxxxx)" 
                               style="flex: 1; padding: 12px 16px; font-size: 1rem; border-radius: 12px;"
                               onkeydown="if(event.key==='Enter') ChannelsAccountsPage.parseAuthorLink()">
                        <button class="btn btn-secondary" onclick="ChannelsAccountsPage.pasteFromClipboard()" style="padding: 12px 20px; font-weight: 500;">
                            📋 粘贴
                        </button>
                        <button class="btn btn-primary" id="btn-parse-author" onclick="ChannelsAccountsPage.parseAuthorLink()" style="min-width: 120px; display: flex; align-items: center; justify-content: center; gap: 8px;">
                            <span>解析作者</span>
                        </button>
                    </div>
                    
                    <div style="margin-top: 12px; font-size: 0.85rem; color: var(--text-muted); display: flex; align-items: center; justify-content: space-between;">
                        <span>支持包含视频号链接 of 混合文本</span>
                        <a href="javascript:void(0)" onclick="ChannelsAccountsPage.toggleManualAdd()" style="color: var(--primary); text-decoration: none; font-weight: 500;">⚡ 高级：直接输入作者 ID 收藏</a>
                    </div>
                </div>

                <!-- 手动输入 ID 收藏折叠栏 -->
                <div id="manual-add-container" style="display: none; margin-top: var(--spacing-md); padding-top: var(--spacing-md); border-top: 1px dashed var(--border-color);">
                    <div style="display: flex; gap: var(--spacing-sm); align-items: center; flex-wrap: wrap;">
                        <input type="text" id="manual-username-input" class="form-input" placeholder="输入作者 ID (如 v2_xxx@finder)" style="flex: 1; min-width: 250px; font-family: monospace; font-size: 0.9rem;">
                        <input type="text" id="manual-nickname-input" class="form-input" placeholder="输入作者昵称/备注" style="width: 200px;">
                        <button class="btn btn-secondary" onclick="ChannelsAccountsPage.addManualFavorite()" style="white-space: nowrap; font-weight: 500;">直接收藏</button>
                    </div>
                    <div class="form-hint" style="margin-top: 6px; color: #ff9900; font-size: 0.8rem;">提示：直接使用 ID 收藏时，头像将采用默认占位图，且不自动拉取视频作品。</div>
                </div>

                <!-- 状态指示 -->
                <div id="author-parse-status" style="display: none; margin-top: var(--spacing-md); padding: 12px; border-radius: 8px; font-size: 0.9rem; text-align: center; background: rgba(0,0,0,0.02);">
                    <span class="spinner" style="width: 16px; height: 16px; border-width: 2px; display: inline-block; vertical-align: middle; margin-right: 8px;"></span>
                    <span id="author-parse-status-text">正在发起云端解析...</span>
                </div>

                <!-- 解析出的作者卡片 -->
                <div id="resolved-author-card" style="display: none; margin-top: var(--spacing-lg); padding: var(--spacing-md); background: rgba(7,193,96,0.03); border: 1.5px solid rgba(7,193,96,0.15); border-radius: 12px; align-items: center; justify-content: space-between; gap: var(--spacing-md); flex-wrap: wrap;">
                    <!-- 动态渲染 -->
                </div>
            </div>

            <!-- 已收藏创作者列表 -->
            <div class="card animate-fade-in">
                <div class="card-header" style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: var(--spacing-md); margin-bottom: var(--spacing-md);">
                    <h3 class="card-title" style="margin: 0; display: flex; align-items: center; gap: 8px;">
                        👥 已收藏创作者
                        <span id="favorites-count-badge" class="badge" style="background: var(--primary); color: white; font-size: 0.8rem; border-radius: 20px; padding: 2px 8px;">0</span>
                    </h3>
                    <button class="btn btn-secondary btn-sm" onclick="ChannelsAccountsPage.loadFavorites()" style="font-size: 0.85rem; padding: 6px 12px; font-weight: 500;">🔄 刷新列表</button>
                </div>

                <!-- 创作者网格 -->
                <div id="favorites-grid" class="card-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--spacing-md); margin-top: var(--spacing-lg);">
                    <!-- 动态渲染 -->
                </div>

                <!-- 空状态 -->
                <div id="favorites-empty" class="empty-state" style="display: none; padding: 60px 24px; text-align: center;">
                    <div class="empty-state-icon" style="color: var(--text-muted); margin-bottom: 16px;">
                        <svg viewBox="0 0 24 24" fill="none" width="60" height="60" stroke="currentColor" stroke-width="1.5">
                            <path d="M17 21V19C17 16.79 15.21 15 13 15H5C2.79 15 1 16.79 1 19V21"/>
                            <circle cx="9" cy="7" r="4"/>
                        </svg>
                    </div>
                    <div class="empty-state-title" style="font-size: 1.1rem; font-weight: 600; color: var(--text-primary);">暂无收藏的作者</div>
                    <div class="empty-state-desc" style="color: var(--text-muted); font-size: 0.9rem; margin-top: 4px;">在上方粘贴作者任意视频的分享链接，解析并加入收藏。</div>
                </div>
            </div>
        `;
    },

    destroy() {
        this.favorites = [];
        this.resolvedAuthor = null;
        this.isParsing = false;
    },

    async init() {
        await this.loadFavorites();
        await this.checkProxyStatus();
    },

    async pasteFromClipboard() {
        try {
            const text = await navigator.clipboard.readText();
            const input = document.getElementById('channels-author-url-input');
            if (input) {
                input.value = text.trim();
                Toast.success('已粘贴剪贴板内容');
                input.focus();
            }
        } catch (err) {
            Toast.warning('无法读取剪贴板，请手动粘贴');
        }
    },

    toggleManualAdd() {
        const container = document.getElementById('manual-add-container');
        if (container) {
            const isHidden = container.style.display === 'none';
            container.style.display = isHidden ? 'block' : 'none';
        }
    },

    async parseAuthorLink() {
        if (this.isParsing) return;

        const input = document.getElementById('channels-author-url-input');
        const shareUrl = input?.value.trim();

        if (!shareUrl) {
            Toast.warning('请输入或粘贴视频号链接');
            return;
        }

        const parseBtn = document.getElementById('btn-parse-author');
        const statusDiv = document.getElementById('author-parse-status');
        const resolvedCard = document.getElementById('resolved-author-card');

        if (parseBtn) parseBtn.disabled = true;
        if (statusDiv) statusDiv.style.display = 'block';
        if (resolvedCard) resolvedCard.style.display = 'none';

        this.isParsing = true;

        try {
            const res = await API.channels.fetchVideoProfile(shareUrl);
            const ai = res.data && res.data.authorInfo;

            if (!ai || (!ai.username && !ai.nickname)) {
                throw new Error('解析成功，但未能提取到作者信息（昵称与 ID 均为空）');
            }

            this.resolvedAuthor = {
                username: ai.username || ai.nickname,
                nickname: ai.nickname || '未命名作者',
                head_img_url: ai.headImgUrl || ''
            };

            // 如果有解析的视频本身，顺便把这条视频保存下来作为作者的初始视频
            const fi = res.data.feedInfo;
            if (fi && fi.videoUrl) {
                // 异步存入该作者的作品库，不做阻碍
                const bestVideoUrl = fi.h264VideoInfo?.videoUrl || fi.h265VideoInfo?.videoUrl || fi.videoUrl;
                API.channels.addAuthorVideo(this.resolvedAuthor.username, {
                    id: fi.id || String(Date.now()),
                    description: fi.description || '',
                    cover_url: fi.coverUrl || '',
                    video_url: fi.videoUrl || '',
                    video_url_h264: fi.h264VideoInfo?.videoUrl || '',
                    video_url_h265: fi.h265VideoInfo?.videoUrl || '',
                    createtime: fi.createtime ? String(fi.createtime) : String(Math.floor(Date.now() / 1000)),
                    decode_key: fi.media?.decodeKey || fi.decodeKey || ''
                }).catch(e => console.error('保存初始视频失败:', e));
            }

            this.renderResolvedAuthor();
            Toast.success('成功解析出作者信息！');
        } catch (err) {
            Toast.error('解析作者失败: ' + (err.message || '网络或接口故障'));
        } finally {
            this.isParsing = false;
            if (parseBtn) parseBtn.disabled = false;
            if (statusDiv) statusDiv.style.display = 'none';
        }
    },

    renderResolvedAuthor() {
        const card = document.getElementById('resolved-author-card');
        if (!card || !this.resolvedAuthor) return;

        const defaultAvatar = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23888'><path d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/></svg>";
        const isAlreadyFav = this.favorites.some(fav => fav.username === this.resolvedAuthor.username);

        card.innerHTML = `
            <div style="display: flex; align-items: center; gap: 16px; flex: 1; min-width: 280px;">
                <img src="${this.esc(this.resolvedAuthor.head_img_url)}" alt="${this.esc(this.resolvedAuthor.nickname)}" 
                     style="width: 52px; height: 52px; border-radius: 50%; object-fit: cover; border: 2px solid var(--primary);" 
                     onerror="this.src='${defaultAvatar}'">
                <div style="flex: 1; overflow: hidden;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <strong style="font-size: 1.1rem; color: var(--text-primary);">${this.esc(this.resolvedAuthor.nickname)}</strong>
                        <span class="badge badge-success" style="font-size: 0.75rem; background: var(--primary); color: white; padding: 2px 6px;">解析成功</span>
                    </div>
                    <div style="font-size: 0.8rem; color: var(--text-muted); font-family: monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 4px;" title="${this.esc(this.resolvedAuthor.username)}">
                        ID: ${this.esc(this.resolvedAuthor.username)}
                    </div>
                </div>
            </div>
            <div style="display: flex; gap: var(--spacing-sm); align-items: center; min-width: 220px; justify-content: flex-end;">
                ${isAlreadyFav ? `
                    <button class="btn btn-secondary" disabled style="padding: 10px 18px; font-weight: 500;">✓ 已在收藏列表中</button>
                ` : `
                    <button class="btn btn-primary" onclick="ChannelsAccountsPage.saveResolvedFavorite()" style="padding: 10px 20px; font-weight: 500; display: flex; align-items: center; gap: 6px;">
                        💚 收藏该作者
                    </button>
                `}
                <button class="btn btn-secondary" onclick="Router.navigate('channels_user?username=${this.esc(this.resolvedAuthor.username)}')" style="padding: 10px 16px; font-weight: 500;">
                    直接进入主页
                </button>
            </div>
        `;
        card.style.display = 'flex';
    },

    async saveResolvedFavorite() {
        if (!this.resolvedAuthor) return;
        try {
            await API.channels.addFavorite(this.resolvedAuthor);
            Toast.success(`已收藏作者: ${this.resolvedAuthor.nickname}`);
            
            // 隐藏解析卡片并清空输入框
            const card = document.getElementById('resolved-author-card');
            if (card) card.style.display = 'none';
            const input = document.getElementById('channels-author-url-input');
            if (input) input.value = '';

            this.resolvedAuthor = null;
            await this.loadFavorites();
        } catch (err) {
            Toast.error('收藏失败: ' + err.message);
        }
    },

    async addManualFavorite() {
        const usernameInput = document.getElementById('manual-username-input');
        const nicknameInput = document.getElementById('manual-nickname-input');
        const username = usernameInput?.value.trim();
        const nickname = nicknameInput?.value.trim() || '未解析创作者';

        if (!username) {
            Toast.warning('请输入作者 ID');
            return;
        }

        try {
            await API.channels.addFavorite({
                username: username,
                nickname: nickname,
                head_img_url: ''
            });

            Toast.success('手动收藏成功！');
            if (usernameInput) usernameInput.value = '';
            if (nicknameInput) nicknameInput.value = '';
            
            // 折叠回手动收藏区域
            this.toggleManualAdd();
            await this.loadFavorites();
        } catch (err) {
            Toast.error('手动收藏失败: ' + err.message);
        }
    },

    async loadFavorites() {
        try {
            const favs = await API.channels.getFavorites();
            this.favorites = favs || [];
            this.renderFavoritesGrid();
        } catch (err) {
            console.error('获取收藏列表失败:', err);
            Toast.error('获取收藏列表失败');
        }
    },

    renderFavoritesGrid() {
        const grid = document.getElementById('favorites-grid');
        const empty = document.getElementById('favorites-empty');
        const badge = document.getElementById('favorites-count-badge');

        if (badge) badge.textContent = this.favorites.length;

        if (!grid || !empty) return;

        if (this.favorites.length === 0) {
            grid.style.display = 'none';
            empty.style.display = 'block';
            return;
        }

        empty.style.display = 'none';
        grid.style.display = 'grid';

        const defaultAvatar = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23888'><path d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/></svg>";

        grid.innerHTML = this.favorites.map(fav => {
            return `
                <div class="favorite-card card" 
                     style="display: flex; flex-direction: column; justify-content: space-between; padding: var(--spacing-md); cursor: pointer; transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s; position: relative; border-radius: 12px; overflow: hidden; border: 1.5px solid var(--border-color); background: var(--bg-card);"
                     onmouseenter="this.style.transform='translateY(-4px)'; this.style.borderColor='var(--primary)'; this.style.boxShadow='var(--shadow-md)';"
                     onmouseleave="this.style.transform=''; this.style.borderColor='var(--border-color)'; this.style.boxShadow='';"
                     onclick="if(event.target.closest('.action-btn')) return; Router.navigate('channels_user?username=${this.esc(fav.username)}')">
                    
                    <div style="display: flex; gap: var(--spacing-sm); align-items: center;">
                        <img src="${this.esc(fav.head_img_url)}" alt="${this.esc(fav.nickname)}" 
                             style="width: 52px; height: 52px; border-radius: 50%; object-fit: cover; border: 1.5px solid rgba(0,0,0,0.05);" 
                             onerror="this.src='${defaultAvatar}'">
                        <div style="flex: 1; overflow: hidden;">
                            <h4 style="margin: 0; font-size: 1.05rem; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${this.esc(fav.nickname)}</h4>
                            <p style="margin: 4px 0 0 0; font-family: monospace; font-size: 0.75rem; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${this.esc(fav.username)}">ID: ${this.esc(fav.username)}</p>
                        </div>
                    </div>

                    <div style="display: flex; gap: var(--spacing-xs); justify-content: flex-end; margin-top: var(--spacing-md); border-top: 1px solid var(--border-color); padding-top: var(--spacing-sm);">
                        <button class="btn btn-secondary btn-sm action-btn" onclick="ChannelsAccountsPage.removeFavoriteConfirm('${this.esc(fav.username)}', '${this.esc(fav.nickname)}')" style="font-size: 0.8rem; padding: 4px 10px; border-radius: 6px; color: #ff3b30; border-color: rgba(255,59,48,0.2);">
                            🗑️ 取消收藏
                        </button>
                        <button class="btn btn-primary btn-sm action-btn" onclick="Router.navigate('channels_user?username=${this.esc(fav.username)}')" style="font-size: 0.8rem; padding: 4px 12px; border-radius: 6px; font-weight: 500;">
                            进入主页 ➔
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    },

    removeFavoriteConfirm(username, nickname) {
        Modal.open({
            title: '取消收藏确认',
            content: `<p style="color: var(--text-secondary)">您确定要取消收藏创作者“${this.esc(nickname)}”吗？<br><span style="font-size:0.8rem;color:var(--text-muted);display:block;margin-top:6px;">取消收藏后，作者已下载的作品历史记录不会被删除，但将不再展示在创作者管理列表中。</span></p>`,
            footer: `
                <button class="btn btn-secondary" onclick="Modal.close()">暂不取消</button>
                <button class="btn btn-primary" id="confirm-unfavorite-btn" style="background: #ff3b30; color: white; border-color: rgba(255,59,48,0.2); font-weight: 500;">确认取消</button>
            `
        });
        const confirmBtn = document.getElementById('confirm-unfavorite-btn');
        if (confirmBtn) {
            confirmBtn.onclick = async () => {
                Modal.close();
                try {
                    await API.channels.removeFavorite(username);
                    Toast.success('取消收藏成功');
                    await this.loadFavorites();
                } catch (err) {
                    Toast.error('取消收藏失败: ' + err.message);
                }
            };
        }
    },

    esc(s) {
        if (!s) return "";
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
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
    }
};
