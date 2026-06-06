/**
 * 视频号作者主页组件
 */
const ChannelsUserPage = {
    username: '',
    authorInfo: null,
    videos: [],
    history: [],
    selectedIds: new Set(),
    isParsingBatch: false,
    isDownloadingBatch: false,
    isBatchDownloadingCanceled: false,

    render() {
        return `
            <div id="channels-user-page-container">
                <!-- 创作者详情视图 -->
                <div id="channels-user-profile-section" style="display: none;">
                    <div class="page-header animate-fade-in" style="display: flex; justify-content: space-between; align-items: center; gap: var(--spacing-md); flex-wrap: wrap;">
                        <div>
                            <h2 class="page-title">作者主页</h2>
                            <p class="page-description">查看创作者已解析的作品、批量粘贴视频链接进行解析 and 收藏管理。</p>
                        </div>
                        <button class="btn btn-secondary" onclick="Router.navigate('channels_user')" style="display: flex; align-items: center; gap: 6px; font-weight: 500;">
                            ⬅ 返回作者列表
                        </button>
                    </div>

                    <!-- 作者信息头部卡片 -->
                    <div id="channels-user-header-card" class="card animate-fade-in" style="margin-top: var(--spacing-lg); margin-bottom: var(--spacing-lg);">
                        <div style="display: flex; gap: var(--spacing-lg); align-items: center; flex-wrap: wrap; padding: var(--spacing-sm);">
                            <img id="channels-user-avatar" src="" alt="作者头像" 
                                 style="width: 80px; height: 80px; border-radius: 50%; object-fit: cover; border: 3px solid rgba(7, 193, 96, 0.2);"
                                 onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' viewBox=\\'0 0 24 24\\' fill=\\'%23888\\'><path d=\\'M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z\\'/></svg>'">
                            <div style="flex: 1; min-width: 250px;">
                                <h2 id="channels-user-nickname" style="font-size: 1.4rem; font-weight: 700; margin: 0; display: flex; align-items: center; gap: 8px;">加载中...</h2>
                                <p id="channels-user-id-text" style="font-family: monospace; font-size: 0.85rem; color: var(--text-muted); margin: 6px 0 0 0; word-break: break-all;">ID: -</p>
                            </div>
                            <div style="display: flex; gap: var(--spacing-sm); align-items: center;">
                                <button class="btn btn-secondary" onclick="ChannelsUserPage.loadAuthorVideos()" style="font-weight: 500; display: flex; align-items: center; gap: 6px;">
                                    🔄 刷新作品
                                </button>
                                <button class="btn btn-secondary" onclick="ChannelsUserPage.toggleFavoriteStatus()" id="btn-user-fav-toggle" style="font-weight: 500;">
                                    💚 取消收藏
                                </button>
                                <button class="btn btn-primary" onclick="ChannelsUserPage.openFolder()" style="font-weight: 500;">
                                    📂 浏览本地文件
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- 批量导入与解析面板 -->
                    <div class="card animate-fade-in" style="margin-bottom: var(--spacing-lg);">
                        <details id="batch-import-details" style="cursor: pointer;">
                            <summary style="font-weight: 600; font-size: 1.05rem; padding: 4px 0; outline: none; color: var(--text-primary); display: flex; align-items: center; justify-content: space-between; user-select: none;">
                                <span>🔗 批量导入并解析视频链接 (点击展开/折叠)</span>
                                <span style="color: var(--primary); font-size: 0.85rem; font-weight: 500;">展开工具栏 ▾</span>
                            </summary>
                            <div style="margin-top: var(--spacing-md); border-top: 1px solid rgba(0,0,0,0.05); padding-top: var(--spacing-md); cursor: default;" onclick="event.stopPropagation()">
                                <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 8px;">
                                    请在下方框中粘贴多条视频号分享链接，每行一条（系统会自动提取含有视频号链接的内容）。
                                </p>
                                <textarea id="channels-batch-urls-textarea" class="form-textarea" rows="6" 
                                          placeholder="粘贴分享文本或链接，例如：&#10;https://weixin.qq.com/sph/xxxxx&#10;https://weixin.qq.com/sph/yyyyy" 
                                          style="width: 100%; font-family: monospace; font-size: 0.9rem; padding: 12px; border-radius: 10px; margin-bottom: var(--spacing-sm); resize: vertical;"></textarea>
                                
                                <!-- 批量解析状态展示 -->
                                <div id="batch-parse-status-panel" style="display: none; padding: var(--spacing-sm) var(--spacing-md); background: rgba(0,0,0,0.02); border-radius: 8px; margin-bottom: var(--spacing-sm); font-size: 0.9rem;">
                                    <div style="display: flex; align-items: center; gap: 8px;">
                                        <span class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></span>
                                        <span id="batch-parse-status-msg" style="font-weight: 500;">正在提取链接...</span>
                                    </div>
                                </div>

                                <div style="display: flex; gap: var(--spacing-sm);">
                                    <button class="btn btn-primary" id="btn-batch-parse" onclick="ChannelsUserPage.startBatchParsing()" style="min-width: 120px; font-weight: 500;">
                                        🚀 开始批量解析
                                    </button>
                                    <button class="btn btn-secondary" onclick="ChannelsUserPage.clearBatchTextarea()">
                                        清空
                                    </button>
                                </div>
                            </div>
                        </details>
                    </div>

                    <!-- 作品管理卡片 -->
                    <div class="card animate-fade-in">
                        <!-- 头部操作栏 -->
                        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: var(--spacing-md); margin-bottom: var(--spacing-md); flex-wrap: wrap; gap: var(--spacing-sm);">
                            <div style="display: flex; align-items: center; gap: 12px;">
                                <input type="checkbox" id="check-all-videos" onchange="ChannelsUserPage.toggleSelectAll(this)" style="width: 18px; height: 18px; cursor: pointer;">
                                <label for="check-all-videos" style="font-weight: 600; cursor: pointer; user-select: none;">全选作品</label>
                                <span id="selected-summary-text" style="font-size: 0.85rem; color: var(--text-muted);">已选 0 个视频</span>
                            </div>
                            <div style="display: flex; gap: var(--spacing-md); align-items: center; flex-wrap: wrap;">
                                <div style="display: flex; align-items: center; gap: 6px;">
                                    <span style="font-size: 0.85rem; color: var(--text-secondary);">下载画质:</span>
                                    <select id="user-quality-select" class="form-select" style="font-size: 0.85rem; padding: 6px 10px; border-radius: 8px; background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-primary); width: 160px; outline: none;">
                                        <option value="raw">原始视频 (最高画质)</option>
                                        <option value="h265">H265 极高画质 (HEVC)</option>
                                        <option value="h264">H264 标准兼容 (AVC)</option>
                                        <option value="default" selected>默认画质</option>
                                    </select>
                                </div>
                                <button class="btn btn-primary btn-sm" id="btn-batch-download" onclick="ChannelsUserPage.downloadSelected()" style="font-size: 0.85rem; padding: 6px 16px; font-weight: 500;" disabled>
                                    📥 批量下载已选 (0)
                                </button>
                            </div>
                        </div>

                        <!-- 视频作品网格 -->
                        <div id="channels-user-videos-grid" class="video-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--spacing-md); margin-top: var(--spacing-md);">
                            <!-- 动态载入 -->
                        </div>

                        <!-- 空作品状态 -->
                        <div id="channels-user-videos-empty" class="empty-state" style="display: none; padding: 80px 24px; text-align: center;">
                            <div class="empty-state-icon" style="color: var(--text-muted); margin-bottom: 16px;">
                                <svg viewBox="0 0 24 24" fill="none" width="60" height="60" stroke="currentColor" stroke-width="1.5">
                                    <rect x="3" y="5" width="18" height="14" rx="2"/>
                                    <path d="M10 9l5 3-5 3V9z" fill="currentColor"/>
                                </svg>
                            </div>
                            <div class="empty-state-title" style="font-size: 1.1rem; font-weight: 600; color: var(--text-primary);">没有已解析的作品</div>
                            <div class="empty-state-desc" style="color: var(--text-muted); font-size: 0.9rem; margin-top: 4px;">
                                当前创作者还没有已解析的作品。请使用上方的批量导入工具粘贴其视频链接进行解析。
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 创作者选择视图 (未提供 ID 时显示) -->
                <div id="channels-user-selector-section" style="display: none;">
                    <div class="page-header animate-fade-in">
                        <h2 class="page-title">作者主页</h2>
                        <p class="page-description">选择一个已收藏的视频号创作者，以查看其主页及作品列表。</p>
                    </div>

                    <div class="card animate-fade-in" style="margin-top: var(--spacing-lg);">
                        <div class="card-header" style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: var(--spacing-md); margin-bottom: var(--spacing-md);">
                            <h3 class="card-title" style="margin: 0; display: flex; align-items: center; gap: 8px;">
                                👥 已收藏创作者
                            </h3>
                            <button class="btn btn-secondary btn-sm" onclick="Router.navigate('channels_accounts')" style="font-size: 0.85rem; padding: 6px 12px; font-weight: 500;">
                                ➕ 管理创作者/添加新作者
                            </button>
                        </div>

                        <!-- 创作者选择列表 -->
                        <div id="selector-favorites-grid" class="card-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--spacing-md); margin-top: var(--spacing-lg);">
                            <!-- 动态加载 -->
                        </div>

                        <!-- 空状态 -->
                        <div id="selector-favorites-empty" class="empty-state" style="display: none; padding: 60px 24px; text-align: center;">
                            <div class="empty-state-icon" style="color: var(--text-muted); margin-bottom: 16px;">
                                <svg viewBox="0 0 24 24" fill="none" width="60" height="60" stroke="currentColor" stroke-width="1.5">
                                    <path d="M17 21V19C17 16.79 15.21 15 13 15H5C2.79 15 1 16.79 1 19V21"/>
                                    <circle cx="9" cy="7" r="4"/>
                                </svg>
                            </div>
                            <div class="empty-state-title" style="font-size: 1.1rem; font-weight: 600; color: var(--text-primary);">暂无收藏的作者</div>
                            <div class="empty-state-desc" style="color: var(--text-muted); font-size: 0.9rem; margin-top: 4px;">请先去“视频号管理”页面解析并添加创作者。</div>
                            <button class="btn btn-primary" onclick="Router.navigate('channels_accounts')" style="margin-top: 16px;">
                                进入视频号管理
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
    },

    destroy() {
        this.username = '';
        this.authorInfo = null;
        this.videos = [];
        this.history = [];
        this.selectedIds.clear();
        this.isParsingBatch = false;
        this.isDownloadingBatch = false;
        this.isBatchDownloadingCanceled = false;
    },

    async onShow() {
        await this.init();
    },

    async init() {
        const hash = window.location.hash;
        const queryString = hash.includes('?') ? hash.split('?')[1] : '';
        const urlParams = new URLSearchParams(queryString || window.location.search);
        const username = urlParams.get('username');

        const profileSection = document.getElementById('channels-user-profile-section');
        const selectorSection = document.getElementById('channels-user-selector-section');

        if (!username) {
            if (profileSection) profileSection.style.display = 'none';
            if (selectorSection) selectorSection.style.display = 'block';
            await this.loadSelectorFavorites();
            return;
        }

        if (profileSection) profileSection.style.display = 'block';
        if (selectorSection) selectorSection.style.display = 'none';

        this.username = username;
        this.selectedIds.clear();

        await this.loadAuthorDetails();
        await this.loadHistory();
        await this.loadAuthorVideos();
    },

    async loadAuthorDetails() {
        try {
            // 从收藏列表中获取该作者的头像和昵称
            const favs = await API.channels.getFavorites();
            const author = favs.find(f => f.username === this.username);

            if (author) {
                this.authorInfo = author;
            } else {
                // 如果未在收藏中，可能是临时查看
                this.authorInfo = {
                    username: this.username,
                    nickname: '未收藏作者',
                    head_img_url: ''
                };
            }

            // 渲染头部信息
            const avatarEl = document.getElementById('channels-user-avatar');
            const nicknameEl = document.getElementById('channels-user-nickname');
            const idEl = document.getElementById('channels-user-id-text');
            const favBtn = document.getElementById('btn-user-fav-toggle');

            if (avatarEl && this.authorInfo.head_img_url) {
                avatarEl.src = this.authorInfo.head_img_url;
            }
            if (nicknameEl) {
                nicknameEl.textContent = this.authorInfo.nickname;
            }
            if (idEl) {
                idEl.textContent = `ID: ${this.username}`;
            }
            if (favBtn) {
                const isFav = favs.some(f => f.username === this.username);
                favBtn.textContent = isFav ? '💚 取消收藏' : '➕ 收藏作者';
                favBtn.className = isFav ? 'btn btn-secondary' : 'btn btn-primary';
            }
        } catch (err) {
            console.error('加载作者详情失败:', err);
        }
    },

    async toggleFavoriteStatus() {
        if (!this.authorInfo) return;
        try {
            const favs = await API.channels.getFavorites();
            const isFav = favs.some(f => f.username === this.username);

            if (isFav) {
                await API.channels.removeFavorite(this.username);
                Toast.success('已取消收藏该作者');
            } else {
                await API.channels.addFavorite(this.authorInfo);
                Toast.success('收藏作者成功！');
            }
            await this.loadAuthorDetails();
        } catch (err) {
            Toast.error('操作失败: ' + err.message);
        }
    },

    async loadHistory() {
        try {
            this.history = await API.channels.getHistory();
        } catch (e) {
            console.error('加载视频号历史失败:', e);
        }
    },

    async loadAuthorVideos() {
        const grid = document.getElementById('channels-user-videos-grid');
        const empty = document.getElementById('channels-user-videos-empty');
        if (!grid || !empty) return;

        grid.innerHTML = '<div class="spinner" style="grid-column: 1/-1; margin: 40px auto;"></div>';
        empty.style.display = 'none';

        try {
            const vids = await API.channels.getAuthorVideos(this.username);
            // 按照发布时间降序排列
            this.videos = (vids || []).sort((a, b) => Number(b.createtime || 0) - Number(a.createtime || 0));
            this.renderVideos();
        } catch (err) {
            grid.innerHTML = '';
            empty.style.display = 'block';
            empty.querySelector('.empty-state-title').textContent = '加载作品列表失败';
            empty.querySelector('.empty-state-desc').textContent = err.message || '网络通讯异常';
        }
    },

    async loadSelectorFavorites() {
        const grid = document.getElementById('selector-favorites-grid');
        const empty = document.getElementById('selector-favorites-empty');
        if (!grid || !empty) return;

        grid.innerHTML = '<div class="spinner" style="grid-column: 1/-1; margin: 40px auto;"></div>';
        empty.style.display = 'none';

        try {
            const favs = await API.channels.getFavorites();
            if (!favs || favs.length === 0) {
                grid.style.display = 'none';
                empty.style.display = 'block';
                return;
            }

            empty.style.display = 'none';
            grid.style.display = 'grid';

            const defaultAvatar = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23888'><path d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/></svg>";

            grid.innerHTML = favs.map(fav => {
                return `
                    <div class="favorite-card card" 
                         style="display: flex; gap: var(--spacing-sm); align-items: center; padding: var(--spacing-md); cursor: pointer; transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s; border-radius: 12px; border: 1.5px solid var(--border-color); background: var(--bg-card);"
                         onmouseenter="this.style.transform='translateY(-4px)'; this.style.borderColor='var(--primary)'; this.style.boxShadow='var(--shadow-md)';"
                         onmouseleave="this.style.transform=''; this.style.borderColor='var(--border-color)'; this.style.boxShadow='';"
                         onclick="Router.navigate('channels_user?username=${this.esc(fav.username)}')">
                        <img src="${this.esc(fav.head_img_url)}" alt="${this.esc(fav.nickname)}" 
                             style="width: 52px; height: 52px; border-radius: 50%; object-fit: cover; border: 1.5px solid rgba(0,0,0,0.05);" 
                             onerror="this.src='${defaultAvatar}'">
                        <div style="flex: 1; overflow: hidden;">
                            <h4 style="margin: 0; font-size: 1.05rem; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${this.esc(fav.nickname)}</h4>
                            <p style="margin: 4px 0 0 0; font-family: monospace; font-size: 0.75rem; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${this.esc(fav.username)}">ID: ${this.esc(fav.username)}</p>
                        </div>
                    </div>
                `;
            }).join('');
        } catch (err) {
            console.error('加载选择列表失败:', err);
            grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--error); padding: var(--spacing-xl);">加载收藏作者列表失败: ${err.message}</div>`;
        }
    },

    renderVideos() {
        const grid = document.getElementById('channels-user-videos-grid');
        const empty = document.getElementById('channels-user-videos-empty');

        if (!grid || !empty) return;

        grid.innerHTML = '';

        if (this.videos.length === 0) {
            grid.style.display = 'none';
            empty.style.display = 'block';
            this.updateBatchButtonState();
            return;
        }

        empty.style.display = 'none';
        grid.style.display = 'grid';

        const defaultCover = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%23555'><rect width='100%' height='100%' fill='%23f0f0f0'/><path d='M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z' fill='%23aaa'/></svg>";

        grid.innerHTML = this.videos.map(video => {
            const isChecked = this.selectedIds.has(video.id);
            const pubDate = this.formatDate(video.createtime);
            
            // 检查历史记录中是否有该视频
            const downloadedItem = this.history.find(h => {
                // 如果能用 title / url 匹配
                return h.title === (video.description || video.id) || (h.path && h.path.includes(video.description));
            });
            const isDownloaded = !!downloadedItem;

            return `
                <div class="video-card card" 
                     style="display: flex; flex-direction: column; justify-content: space-between; border-radius: 12px; overflow: hidden; background: var(--bg-card); transition: transform 0.2s, box-shadow 0.2s; border: 1.5px solid ${isChecked ? 'var(--primary)' : 'var(--border-color)'}; cursor: pointer; position: relative;"
                     onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='var(--shadow-md)';"
                     onmouseleave="this.style.transform=''; this.style.boxShadow='';"
                     onclick="ChannelsUserPage.handleCardClick(event, '${this.esc(video.id)}')">
                    
                    <!-- 卡片顶部 cover 图案及选择框 -->
                    <div style="position: relative; padding-top: 56.25%; background: rgba(0,0,0,0.05); overflow: hidden;">
                        <img src="${this.esc(video.cover_url)}" alt="${this.esc(video.description)}" 
                             style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;"
                             onerror="this.src='${defaultCover}';">
                        
                        <!-- 选取复选框 -->
                        <div style="position: absolute; top: 10px; left: 10px; z-index: 5;" onclick="event.stopPropagation()">
                            <input type="checkbox" class="video-item-checkbox" data-id="${this.esc(video.id)}" 
                                   ${isChecked ? 'checked' : ''} 
                                   onchange="ChannelsUserPage.toggleSelectVideo('${this.esc(video.id)}', this.checked)"
                                   style="width: 20px; height: 20px; cursor: pointer; filter: drop-shadow(0 2px 4px rgba(0,0,0,0.3));">
                        </div>

                        <!-- 下载完毕角标 -->
                        ${isDownloaded ? `
                            <span class="badge badge-success" style="position: absolute; top: 10px; right: 10px; z-index: 5; background: var(--primary); color: white; border-radius: 4px; font-size: 0.75rem; padding: 2px 6px; box-shadow: 0 2px 4px rgba(0,0,0,0.2);">
                                已下载
                            </span>
                        ` : ''}

                        <!-- 播放角标 (如果已下载且有本地路径) -->
                        ${isDownloaded && downloadedItem.path ? `
                            <div class="action-btn" onclick="event.stopPropagation(); ChannelsUserPage.playLocalVideo('${this.esc(downloadedItem.path)}')" 
                                 style="position: absolute; top: calc(50% - 22px); left: calc(50% - 22px); width: 44px; height: 44px; background: rgba(7,193,96,0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; color: white; cursor: pointer; box-shadow: 0 4px 10px rgba(0,0,0,0.3); transition: transform 0.2s;"
                                 onmouseenter="this.style.transform='scale(1.1)';" onmouseleave="this.style.transform='scale(1)';">
                                <svg viewBox="0 0 24 24" fill="currentColor" width="24" height="24">
                                    <path d="M8 5v14l11-7z"/>
                                </svg>
                            </div>
                        ` : ''}
                    </div>

                    <!-- 视频文字部分 -->
                    <div style="padding: var(--spacing-md); display: flex; flex-direction: column; flex: 1; justify-content: space-between;">
                        <div>
                            <h3 style="font-size: 0.95rem; font-weight: 600; margin: 0 0 6px 0; color: var(--text-primary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; line-height: 1.4;" title="${this.esc(video.description || '无标题')}">
                                ${this.esc(video.description || '无标题')}
                            </h3>
                            <div style="font-size: 0.75rem; color: var(--text-muted); margin-bottom: 12px;">
                                📅 发布时间: ${pubDate}
                            </div>
                        </div>

                        <!-- 底部动作条 -->
                        <div style="display: flex; gap: var(--spacing-xs); margin-top: auto; border-top: 1px solid rgba(0,0,0,0.05); padding-top: var(--spacing-sm);" onclick="event.stopPropagation()">
                            ${isDownloaded && downloadedItem.path ? `
                                <button class="btn btn-secondary btn-sm" onclick="ChannelsUserPage.openLocalParent('${this.esc(downloadedItem.path)}')" style="flex: 1; font-size: 0.8rem; padding: 6px; border-radius: 6px;">
                                    📂 定位文件夹
                                </button>
                            ` : `
                                <button class="btn btn-primary btn-sm" onclick="ChannelsUserPage.downloadSingleVideo('${this.esc(video.id)}')" style="flex: 1; font-size: 0.8rem; padding: 6px; border-radius: 6px; font-weight: 500;">
                                    📥 立即下载
                                </button>
                            `}
                            <button class="btn btn-secondary btn-sm" onclick="ChannelsUserPage.copyVideoUrl('${this.esc(video.id)}')" style="font-size: 0.8rem; padding: 6px; border-radius: 6px;" title="复制直链">
                                🔗 复制链接
                            </button>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        this.updateBatchButtonState();
    },

    handleCardClick(event, videoId) {
        // 如果点击的是 checkbox、button 或者 action-btn 标签，不触发选中状态切换
        if (event.target.closest('input[type="checkbox"]') || event.target.closest('button') || event.target.closest('.action-btn')) {
            return;
        }
        const checkbox = event.currentTarget.querySelector('.video-item-checkbox');
        if (checkbox) {
            checkbox.checked = !checkbox.checked;
            this.toggleSelectVideo(videoId, checkbox.checked);
        }
    },

    toggleSelectVideo(videoId, isChecked) {
        if (isChecked) {
            this.selectedIds.add(videoId);
        } else {
            this.selectedIds.delete(videoId);
        }
        this.updateBatchButtonState();
    },

    toggleSelectAll(checkbox) {
        const isChecked = checkbox.checked;
        const boxes = document.querySelectorAll('.video-item-checkbox');
        
        boxes.forEach(box => {
            box.checked = isChecked;
            const id = box.getAttribute('data-id');
            if (isChecked) {
                this.selectedIds.add(id);
            } else {
                this.selectedIds.delete(id);
            }
        });

        // 重新渲染卡片外框高亮颜色
        this.videos.forEach(video => {
            const isSel = this.selectedIds.has(video.id);
            // 找到包含 data-id 的 checkbox，然后获取其外层 card
            const el = document.querySelector(`.video-item-checkbox[data-id="${video.id}"]`);
            if (el) {
                const card = el.closest('.video-card');
                if (card) {
                    card.style.borderColor = isSel ? 'var(--primary)' : 'var(--border-color)';
                }
            }
        });

        this.updateBatchButtonState();
    },

    updateBatchButtonState() {
        const btn = document.getElementById('btn-batch-download');
        const summary = document.getElementById('selected-summary-text');
        const checkAll = document.getElementById('check-all-videos');

        if (summary) {
            summary.textContent = `已选 ${this.selectedIds.size} / ${this.videos.length} 个视频`;
        }

        if (btn) {
            btn.disabled = this.selectedIds.size === 0;
            btn.textContent = `📥 批量下载已选 (${this.selectedIds.size})`;
        }

        if (checkAll) {
            checkAll.checked = this.videos.length > 0 && this.selectedIds.size === this.videos.length;
        }
    },

    clearBatchTextarea() {
        const text = document.getElementById('channels-batch-urls-textarea');
        if (text) text.value = '';
    },

    async startBatchParsing() {
        if (this.isParsingBatch) return;

        const textarea = document.getElementById('channels-batch-urls-textarea');
        const text = textarea ? textarea.value.trim() : '';

        if (!text) {
            Toast.warning('请粘贴包含视频号链接的文本');
            return;
        }

        // 正则提取 sph 链接和 channels 的 sf 链接
        const sphLinks = text.match(/https?:\/\/weixin\.qq\.com\/sph\/[a-zA-Z0-9]+/g) || [];
        const sfLinks = text.match(/https?:\/\/channels\.weixin\.qq\.com\/mobile\/sf\/[a-zA-Z0-9_]+/g) || [];
        const urls = [...new Set([...sphLinks, ...sfLinks])];

        if (urls.length === 0) {
            Toast.error('未在粘贴文本中检测到有效的微信视频号链接');
            return;
        }

        const parseBtn = document.getElementById('btn-batch-parse');
        const statusPanel = document.getElementById('batch-parse-status-panel');
        const statusMsg = document.getElementById('batch-parse-status-msg');

        if (parseBtn) parseBtn.disabled = true;
        if (statusPanel) statusPanel.style.display = 'block';

        this.isParsingBatch = true;
        
        let successCount = 0;
        let failCount = 0;

        // 生成并发任务队列 (限制 3 并发)
        const tasks = urls.map((url, idx) => {
            return async () => {
                if (statusMsg) {
                    statusMsg.textContent = `正在解析第 ${idx + 1}/${urls.length} 个视频链接...`;
                }
                try {
                    const res = await API.channels.fetchVideoProfile(url);
                    const fi = res.data && res.data.feedInfo;
                    const ai = res.data && res.data.authorInfo;

                    // 如果当前作者是未解析过的手动收藏 ID，在成功获取真实昵称后自动更新收藏的元数据
                    if (ai && (this.authorInfo.nickname === '未解析创作者' || this.authorInfo.nickname === '未收藏作者' || !this.authorInfo.head_img_url)) {
                        try {
                            const newAuthor = {
                                username: this.username,
                                nickname: ai.nickname || this.authorInfo.nickname,
                                head_img_url: ai.headImgUrl || this.authorInfo.head_img_url
                            };
                            await API.channels.addFavorite(newAuthor);
                            this.authorInfo = newAuthor;
                            
                            // 更新 UI 展示
                            const avatarEl = document.getElementById('channels-user-avatar');
                            const nicknameEl = document.getElementById('channels-user-nickname');
                            if (avatarEl && newAuthor.head_img_url) avatarEl.src = newAuthor.head_img_url;
                            if (nicknameEl) nicknameEl.textContent = newAuthor.nickname;
                        } catch (e) {
                            console.error('自动补全作者信息失败:', e);
                        }
                    }

                    // 保存到后端创作者作品库（使用当前的作者 ID 保存，保证数据准确）
                    const targetUsername = this.username || ai?.username || ai?.nickname;

                    await API.channels.addAuthorVideo(targetUsername, {
                        id: fi.id || String(Date.now() + Math.random()),
                        description: fi.description || '',
                        cover_url: fi.coverUrl || '',
                        video_url: fi.videoUrl || '',
                        video_url_h264: fi.h264VideoInfo?.videoUrl || '',
                        video_url_h265: fi.h265VideoInfo?.videoUrl || '',
                        createtime: fi.createtime ? String(fi.createtime) : String(Math.floor(Date.now() / 1000)),
                        decode_key: fi.media?.decodeKey || fi.decodeKey || ''
                    });

                    successCount++;
                } catch (err) {
                    console.error(`解析视频失败 [${url}]:`, err);
                    failCount++;
                }
            };
        });

        // 限制并发执行函数
        await this.limitConcurrency(tasks, 3);

        Toast.success(`批量解析完成！成功: ${successCount}，失败: ${failCount}`);

        if (parseBtn) parseBtn.disabled = false;
        if (statusPanel) statusPanel.style.display = 'none';
        if (textarea) textarea.value = '';

        this.isParsingBatch = false;

        // 重新载入作品库渲染
        await this.loadAuthorVideos();
    },

    async limitConcurrency(tasks, limit) {
        let active = 0;
        let index = 0;
        const results = [];
        return new Promise((resolve) => {
            function runNext() {
                if (index >= tasks.length && active === 0) {
                    resolve(results);
                    return;
                }
                while (active < limit && index < tasks.length) {
                    const currentIndex = index++;
                    active++;
                    tasks[currentIndex]()
                        .then((res) => {
                            results[currentIndex] = { success: true, value: res };
                        })
                        .catch((err) => {
                            results[currentIndex] = { success: false, error: err };
                        })
                        .finally(() => {
                            active--;
                            runNext();
                        });
                }
            }
            runNext();
        });
    },

    async downloadSingleVideo(videoId) {
        const video = this.videos.find(v => v.id === videoId);
        if (!video) return;

        const qualitySelect = document.getElementById('user-quality-select');
        const chosenQuality = qualitySelect ? qualitySelect.value : 'default';
        let downloadUrl = video.video_url;

        if (chosenQuality === 'raw') {
            downloadUrl = this.getRawVideoUrl(video.video_url || video.video_url_h265 || video.video_url_h264);
        } else if (chosenQuality === 'h265') {
            downloadUrl = video.video_url_h265 || video.video_url_h264 || video.video_url;
        } else if (chosenQuality === 'h264') {
            downloadUrl = video.video_url_h264 || video.video_url_h265 || video.video_url;
        } else {
            downloadUrl = video.video_url || video.video_url_h264 || video.video_url_h265;
        }

        Toast.info('已提交单条下载任务');

        try {
            await API.channels.download(downloadUrl, video.description, video.createtime, video.decode_key);
            Toast.success('视频下载成功！');
            await this.loadHistory();
            this.renderVideos();
        } catch (err) {
            Toast.error('下载失败: ' + err.message);
        }
    },

    async downloadSelected() {
        if (this.selectedIds.size === 0 || this.isDownloadingBatch) return;

        const selectedList = this.videos.filter(v => this.selectedIds.has(v.id));
        this.isDownloadingBatch = true;
        this.isBatchDownloadingCanceled = false;

        const userQuality = document.getElementById('user-quality-select')?.value || 'default';

        // 打开批量下载大屏进度 Overlay Modal
        Modal.open({
            title: '📥 批量下载视频作品',
            content: `
                <div style="background: rgba(0,0,0,0.02); padding: 12px; border-radius: 8px; margin-bottom: var(--spacing-md); text-align: left; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;">
                    <div><strong>创作者: </strong> <span style="color: var(--primary); font-weight: 600;">${this.esc(this.authorInfo.nickname)}</span></div>
                    <div style="display: flex; align-items: center; gap: 6px;">
                        <span style="font-size: 0.85rem; color: var(--text-secondary);">下载画质:</span>
                        <select id="batch-quality-select" class="form-select" style="font-size: 0.85rem; padding: 4px 8px; border-radius: 6px; background: var(--bg-input); border: 1px solid var(--border-color); color: var(--text-primary); width: 180px;">
                            <option value="raw" ${userQuality === 'raw' ? 'selected' : ''}>原始视频 (最高画质)</option>
                            <option value="h265" ${userQuality === 'h265' ? 'selected' : ''}>H265 (HEVC) 极高画质</option>
                            <option value="h264" ${userQuality === 'h264' ? 'selected' : ''}>H264 (AVC) 标准兼容</option>
                            <option value="default" ${userQuality === 'default' ? 'selected' : ''}>默认画质</option>
                        </select>
                    </div>
                </div>
                <div style="background: #eee; border-radius: 8px; height: 16px; overflow: hidden; margin-bottom: 12px; position: relative;">
                    <div id="batch-progress-bar" style="background: var(--primary); height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px;"></div>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.9rem; margin-bottom: var(--spacing-md);">
                    <span id="batch-progress-text" style="font-weight: 500; color: var(--text-primary);">正在准备下载队列...</span>
                    <span id="batch-progress-percent" style="font-weight: 600; color: var(--primary);">0%</span>
                </div>
                <div id="batch-download-logs" style="background: #1e1e1e; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; height: 200px; overflow-y: auto; padding: 12px; font-family: monospace; font-size: 0.8rem; line-height: 1.6; text-align: left; color: #a9b7c6; scrollbar-width: thin;">
                    [SYSTEM] 队列初始化成功，共 ${selectedList.length} 个任务待处理。
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" id="btn-batch-close" onclick="Modal.close()" style="display: none; font-weight: 500;">完成并关闭</button>
                <button class="btn btn-secondary" id="btn-batch-cancel" onclick="ChannelsUserPage.cancelBatchDownloading()" style="background: #ff3b30; color: white; border-color: rgba(255,59,48,0.2); font-weight: 500;">终止下载</button>
            `,
            onClose: () => {
                this.isDownloadingBatch = false;
                // 完成或关闭后重新加载状态
                this.loadHistory().then(() => this.renderVideos());
            }
        });

        const progressBar = document.getElementById('batch-progress-bar');
        const progressText = document.getElementById('batch-progress-text');
        const progressPercent = document.getElementById('batch-progress-percent');
        const logsDiv = document.getElementById('batch-download-logs');
        const cancelBtn = document.getElementById('btn-batch-cancel');
        const closeBtn = document.getElementById('btn-batch-close');

        const appendLog = (msg) => {
            if (logsDiv) {
                const p = document.createElement('div');
                p.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
                logsDiv.appendChild(p);
                logsDiv.scrollTop = logsDiv.scrollHeight;
            }
        };

        let completed = 0;
        let failed = 0;

        for (let i = 0; i < selectedList.length; i++) {
            if (this.isBatchDownloadingCanceled) {
                appendLog('⚠️ 下载已由用户手动终止。');
                break;
            }

            const video = selectedList[i];
            const currentIndex = i + 1;
            const progressVal = Math.round((i / selectedList.length) * 100);

            if (progressBar) progressBar.style.width = `${progressVal}%`;
            if (progressPercent) progressPercent.textContent = `${progressVal}%`;
            if (progressText) progressText.textContent = `正在下载第 ${currentIndex}/${selectedList.length} 个视频...`;

            // 根据选中的画质优先级来获取对应的 URL
            const qualitySelect = document.getElementById('batch-quality-select');
            const chosenQuality = qualitySelect ? qualitySelect.value : 'default';
            let downloadUrl = video.video_url;

            if (chosenQuality === 'raw') {
                downloadUrl = this.getRawVideoUrl(video.video_url || video.video_url_h265 || video.video_url_h264);
            } else if (chosenQuality === 'h265') {
                downloadUrl = video.video_url_h265 || video.video_url_h264 || video.video_url;
            } else if (chosenQuality === 'h264') {
                downloadUrl = video.video_url_h264 || video.video_url_h265 || video.video_url;
            } else {
                downloadUrl = video.video_url || video.video_url_h264 || video.video_url_h265;
            }

            appendLog(`⬇️ (${currentIndex}/${selectedList.length}) 正在下载: ${video.description || '无描述'}`);

            try {
                // 流式下载 + 自动 ISAAC-64 解密
                await API.channels.download(downloadUrl, video.description, video.createtime, video.decode_key);
                completed++;
                appendLog(`✅ (${currentIndex}/${selectedList.length}) 成功！视频已保存并自动解密。`);
            } catch (err) {
                failed++;
                appendLog(`❌ (${currentIndex}/${selectedList.length}) 失败！错误信息: ${err.message || err}`);
            }
        }

        // 最终更新
        const finalPercent = 100;
        if (progressBar) progressBar.style.width = '100%';
        if (progressPercent) progressPercent.textContent = '100%';
        
        if (this.isBatchDownloadingCanceled) {
            if (progressText) progressText.textContent = `下载已终止。成功: ${completed}，失败: ${failed}，未处理: ${selectedList.length - completed - failed}`;
            appendLog(`🛑 批量操作终止。成功: ${completed} 个，失败: ${failed} 个。`);
        } else {
            if (progressText) progressText.textContent = `批量下载完成！成功: ${completed}，失败: ${failed}`;
            appendLog(`🎉 批量下载操作全部处理完成！成功: ${completed} 个，失败: ${failed} 个。`);
        }

        if (cancelBtn) cancelBtn.style.display = 'none';
        if (closeBtn) closeBtn.style.display = 'inline-block';

        this.selectedIds.clear();
        this.isDownloadingBatch = false;
    },

    cancelBatchDownloading() {
        this.isBatchDownloadingCanceled = true;
        const cancelBtn = document.getElementById('btn-batch-cancel');
        if (cancelBtn) {
            cancelBtn.disabled = true;
            cancelBtn.textContent = '正在中止...';
        }
    },

    async playLocalVideo(path) {
        try {
            await API.articles.openFile(path);
            Toast.success('已拉起本地播放器播放视频');
        } catch (e) {
            Toast.error('拉起视频文件失败');
        }
    },

    async openLocalParent(path) {
        try {
            await API.articles.openParent(path);
            Toast.success('已在文件夹中定位视频文件');
        } catch (e) {
            Toast.error('定位文件夹失败');
        }
    },

    async openFolder() {
        try {
            await API.channels.openFolder();
            Toast.success('下载目录已打开');
        } catch (e) {
            Toast.error('打开下载目录失败');
        }
    },

    async copyVideoUrl(videoId) {
        const video = this.videos.find(v => v.id === videoId);
        if (!video) return;

        const qualitySelect = document.getElementById('user-quality-select');
        const chosenQuality = qualitySelect ? qualitySelect.value : 'default';
        let downloadUrl = video.video_url;

        if (chosenQuality === 'raw') {
            downloadUrl = this.getRawVideoUrl(video.video_url || video.video_url_h265 || video.video_url_h264);
        } else if (chosenQuality === 'h265') {
            downloadUrl = video.video_url_h265 || video.video_url_h264 || video.video_url;
        } else if (chosenQuality === 'h264') {
            downloadUrl = video.video_url_h264 || video.video_url_h265 || video.video_url;
        } else {
            downloadUrl = video.video_url || video.video_url_h264 || video.video_url_h265;
        }

        try {
            await navigator.clipboard.writeText(downloadUrl);
            Toast.success('视频直链已成功复制到剪贴板！');
        } catch (e) {
            Toast.warning('复制链接失败，请手动选择复制');
        }
    },

    getRawVideoUrl(url) {
        try {
            const u = new URL(decodeURIComponent(url));
            const filekey = u.searchParams.get("encfilekey");
            const token = u.searchParams.get("token");
            if (filekey && token) {
                const newUrl = new URL(u.origin + u.pathname);
                newUrl.searchParams.set("encfilekey", filekey);
                newUrl.searchParams.set("token", token);
                return newUrl.toString();
            }
        } catch (e) {}
        return url;
    },

    formatDate(timestamp) {
        if (!timestamp) return '未知时间';
        try {
            const d = new Date(parseInt(timestamp) * 1000);
            const pad = (n) => String(n).padStart(2, '0');
            return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
        } catch (e) {
            return '时间格式错误';
        }
    },

    esc(s) {
        if (!s) return "";
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }
};
