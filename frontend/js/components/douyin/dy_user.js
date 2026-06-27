const DyUserPage = {
    user: null,
    videos: [],
    loading: false,
    cursor: 0,
    hasMore: false,
    secUid: '',
    loadingMore: false,
    isSelectMode: false,
    currentTab: 'post',

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">用户主页</h2>
                <p class="page-description">查看用户信息和作品列表</p>
            </div>

            <div id="dy-user-container">
                <div id="dy-user-loading" style="display: none; text-align: center; padding: var(--spacing-xl);">
                    <div class="spinner"></div>
                    <p style="margin-top: var(--spacing-md); color: var(--text-muted);">加载中...</p>
                </div>

                <div id="dy-user-content" style="display: none;">
                    <!-- 用户信息卡片 -->
                    <div class="card" style="margin-bottom: var(--spacing-lg);">
                        <div style="display: flex; gap: var(--spacing-lg); align-items: flex-start;">
                            <img id="dy-user-avatar" src="" alt="用户头像" style="width: 100px; height: 100px; border-radius: 50%; object-fit: cover; border: 3px solid var(--border-color);">
                            <div style="flex: 1;">
                                <h2 id="dy-user-nickname" style="font-size: 1.5rem; margin-bottom: 8px;"></h2>
                                <p id="dy-user-signature" style="color: var(--text-muted); margin-bottom: var(--spacing-md);"></p>
                                <div style="display: flex; gap: var(--spacing-lg); margin-bottom: var(--spacing-md);">
                                    <div>
                                        <span style="font-weight: 600; font-size: 1.2rem;" id="dy-user-following">0</span>
                                        <span style="color: var(--text-muted); margin-left: 4px;">关注</span>
                                    </div>
                                    <div>
                                        <span style="font-weight: 600; font-size: 1.2rem;" id="dy-user-follower">0</span>
                                        <span style="color: var(--text-muted); margin-left: 4px;">粉丝</span>
                                    </div>
                                    <div>
                                        <span style="font-weight: 600; font-size: 1.2rem;" id="dy-user-favorited">0</span>
                                        <span style="color: var(--text-muted); margin-left: 4px;">获赞</span>
                                    </div>
                                </div>
                                <button class="btn btn-primary" onclick="DyUserPage.downloadAll()" id="dy-user-download-btn">
                                    <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px;">
                                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                        <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                        <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                                    </svg>
                                    批量下载全部作品
                                </button>
                            </div>
                        </div>
                    </div>

                    <!-- 作品列表 -->
                    <div class="card">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--spacing-md); flex-wrap: wrap; gap: 12px;">
                            <div class="dy-user-tabs" id="dy-user-tabs-container">
                                <div class="tab-item active" id="tab-post" onclick="DyUserPage.switchTab('post')">作品</div>
                            </div>
                            <div id="dy-user-header-actions" style="display: flex; gap: 8px; align-items: center;"></div>
                        </div>
                        <div id="dy-user-videos" class="video-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--spacing-md);"></div>
                        <div id="dy-user-videos-empty" style="display: none; text-align: center; padding: var(--spacing-xl); color: var(--text-muted);">
                            暂无内容
                        </div>
                        <div id="dy-user-more-container" style="text-align: center; display: none; padding: var(--spacing-md) 0; margin-top: var(--spacing-lg);">
                            <button id="dy-user-more-btn" class="btn btn-secondary" onclick="DyUserPage.loadMore()" style="min-width: 150px;">加载更多</button>
                        </div>
                    </div>
                </div>

                <div id="dy-user-empty" style="display: block; text-align: center; padding: var(--spacing-2xl);">
                    <div style="width: 64px; height: 64px; margin: 0 auto var(--spacing-md); background: rgba(102, 126, 234, 0.1); border-radius: 20px; display: flex; align-items: center; justify-content: center;">
                        <svg viewBox="0 0 24 24" fill="none" style="width: 32px; height: 32px; color: var(--primary);">
                            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                            <circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </div>
                    <p style="font-size: 1.1rem; margin-bottom: 8px;">请先搜索用户</p>
                    <p style="color: var(--text-muted);">通过搜索功能找到用户后查看主页</p>
                </div>
            </div>
        `;
    },

    async init() {
        // 检查是否有传入的 sec_uid
        const hash = window.location.hash;
        const queryString = hash.includes('?') ? hash.split('?')[1] : '';
        const urlParams = new URLSearchParams(queryString || window.location.search);
        const secUid = urlParams.get('sec_uid');

        this.isSelectMode = false;

        if (secUid) {
            await this.loadUser(secUid);
        }
    },

    onShow() {
        if (this.user) {
            fetch('/api/douyin/progress')
                .then(res => res.json())
                .then(data => {
                    this.updateDownloadAllButton(data.status);
                })
                .catch(() => {});
        }
    },

    async loadUser(secUid) {
        this.loading = true;
        this.showLoading();
        this.secUid = secUid;
        this.cursor = 0;
        this.hasMore = false;
        this.videos = [];
        this.isSelectMode = false;
        this.currentTab = 'post';

        // Reset tab UI classes
        const postTab = document.getElementById('tab-post');
        if (postTab) {
            document.querySelectorAll('.tab-item').forEach(el => el.classList.remove('active'));
            postTab.classList.add('active');
        }

        try {
            // 获取用户详情
            const res = await fetch(`/api/douyin/user-detail?sec_uid=${secUid}`);
            const data = await res.json();

            if (data.error) {
                throw new Error(data.error);
            }

            this.user = data.user || data;
            this.renderUser();

            // 获取用户作品
            await this.loadVideos();
        } catch (err) {
            Toast.show(err.message, 'error');
            this.showEmpty();
        } finally {
            this.loading = false;
            this.hideLoading();
        }
    },

    async loadVideos() {
        if (this.loadingMore) return;
        this.loadingMore = true;

        try {
            let url = '';
            if (this.currentTab === 'post') {
                url = `/api/douyin/user-videos?sec_uid=${this.secUid}&max_cursor=${this.cursor}&count=18`;
            } else if (this.currentTab === 'like') {
                url = `/api/douyin/liked?sec_uid=${this.secUid}&max_cursor=${this.cursor}&count=18`;
            } else if (this.currentTab === 'collect') {
                url = `/api/douyin/collected?cursor=${this.cursor}&count=18`;
            } else if (this.currentTab === 'story') {
                url = `/api/douyin/user-stories?sec_uid=${this.secUid}&max_cursor=${this.cursor}&count=18`;
            } else if (this.currentTab === 'mix') {
                url = `/api/douyin/user-mixes?sec_uid=${this.secUid}&cursor=${this.cursor}&count=20`;
            }

            const res = await fetch(url);
            const data = await res.json();

            if (data.error) {
                throw new Error(data.error);
            }

            const newVideos = data.aweme_list || data.mix_infos || [];
            this.videos = this.videos.concat(newVideos);
            this.cursor = data.max_cursor || data.cursor || 0;
            this.hasMore = data.has_more || false;

            this.renderVideos();
        } catch (err) {
            console.error('加载列表失败:', err);
            Toast.show('加载列表失败: ' + err.message, 'error');
        } finally {
            this.loadingMore = false;
        }
    },

    async loadMore() {
        if (this.loadingMore || !this.hasMore) return;
        
        const btn = document.getElementById('dy-user-more-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = '加载中...';
        }
        
        await this.loadVideos();
        
        if (btn) {
            btn.disabled = false;
            btn.textContent = '加载更多';
        }
    },

    switchTab(tab) {
        if (this.currentTab === tab || this.loadingMore) return;

        // Remove active class from all tabs
        document.querySelectorAll('.tab-item').forEach(el => el.classList.remove('active'));
        // Add active class to selected tab
        const tabEl = document.getElementById(`tab-${tab}`);
        if (tabEl) tabEl.classList.add('active');

        this.currentTab = tab;
        this.videos = [];
        this.cursor = 0;
        this.hasMore = false;

        // Reset grid container list
        const grid = document.getElementById('dy-user-videos');
        if (grid) grid.innerHTML = '';

        // 获取并检查任务状态以渲染批量下载/取消下载按钮，使按钮文字和状态跟随 tab 切换刷新
        fetch('/api/douyin/progress')
            .then(res => res.json())
            .then(data => {
                this.updateDownloadAllButton(data.status);
            })
            .catch(() => {});

        this.loadVideos();
    },

    renderUser() {
        document.getElementById('dy-user-empty').style.display = 'none';
        document.getElementById('dy-user-content').style.display = 'block';

        const avatar = this.user.avatar_thumb?.url_list?.[0] || this.user.avatar_larger?.url_list?.[0] || '';
        const nickname = this.user.nickname || '未知用户';
        const signature = this.user.signature || '这个人很懒，什么都没写';
        const following = this.formatNumber(this.user.following_count || 0);
        const follower = this.formatNumber(this.user.follower_count || 0);
        const favorited = this.formatNumber(this.user.total_favorited || 0);

        document.getElementById('dy-user-avatar').src = avatar;
        document.getElementById('dy-user-nickname').textContent = nickname;
        document.getElementById('dy-user-signature').textContent = signature;
        document.getElementById('dy-user-following').textContent = following;
        document.getElementById('dy-user-follower').textContent = follower;
        document.getElementById('dy-user-favorited').textContent = favorited;

        // 动态渲染 Tab 分类
        const tabsContainer = document.getElementById('dy-user-tabs-container');
        if (tabsContainer) {
            let tabsHtml = `<div class="tab-item ${this.currentTab === 'post' ? 'active' : ''}" id="tab-post" onclick="DyUserPage.switchTab('post')">作品</div>`;
            
            if (this.user.is_self) {
                tabsHtml += `
                    <div class="tab-item ${this.currentTab === 'like' ? 'active' : ''}" id="tab-like" onclick="DyUserPage.switchTab('like')">喜欢</div>
                    <div class="tab-item ${this.currentTab === 'collect' ? 'active' : ''}" id="tab-collect" onclick="DyUserPage.switchTab('collect')">收藏</div>
                    <div class="tab-item ${this.currentTab === 'story' ? 'active' : ''}" id="tab-story" onclick="DyUserPage.switchTab('story')">日常</div>
                `;
            } else {
                if (this.user.show_favorite_list) {
                    tabsHtml += `<div class="tab-item ${this.currentTab === 'like' ? 'active' : ''}" id="tab-like" onclick="DyUserPage.switchTab('like')">喜欢</div>`;
                }
                if (this.user.mix_count > 0 || this.user.is_mix_user) {
                    tabsHtml += `<div class="tab-item ${this.currentTab === 'mix' ? 'active' : ''}" id="tab-mix" onclick="DyUserPage.switchTab('mix')">合集</div>`;
                }
                if (this.user.story_tab_empty === false || (this.user.life_story_block && this.user.life_story_block.life_story_block === false)) {
                    tabsHtml += `<div class="tab-item ${this.currentTab === 'story' ? 'active' : ''}" id="tab-story" onclick="DyUserPage.switchTab('story')">日常</div>`;
                }
            }
            tabsContainer.innerHTML = tabsHtml;
        }

        // 获取并检查任务状态以渲染批量下载/取消下载按钮
        fetch('/api/douyin/progress')
            .then(res => res.json())
            .then(data => {
                this.updateDownloadAllButton(data.status);
            })
            .catch(() => {});
    },

    renderVideos() {
        const container = document.getElementById('dy-user-videos');
        const empty = document.getElementById('dy-user-videos-empty');
        const moreContainer = document.getElementById('dy-user-more-container');

        if (this.videos.length === 0) {
            container.style.display = 'none';
            empty.style.display = 'block';
            if (moreContainer) moreContainer.style.display = 'none';
            const actions = document.getElementById('dy-user-header-actions');
            if (actions) actions.innerHTML = '';
            return;
        }

        empty.style.display = 'none';
        container.style.display = 'grid';

        if (this.currentTab === 'mix') {
            container.innerHTML = this.videos.map(mix => this.renderMixCard(mix)).join('');
        } else {
            container.innerHTML = this.videos.map(video => this.renderVideoCard(video)).join('');
        }

        if (moreContainer) {
            moreContainer.style.display = this.hasMore ? 'block' : 'none';
        }

        this.updateHeaderActions();
        this.updateDownloadButton();
    },

    renderMixCard(mix) {
        const title = mix.mix_name || '未命名合集';
        const cover = mix.cover?.url_list?.[0] || '';
        const mixId = mix.mix_id;
        const count = mix.total_aweme_count || 0;
        
        return `
            <div class="video-card" style="border-radius: 12px; overflow: hidden; background: var(--bg-secondary); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer;" onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='0 8px 24px rgba(0,0,0,0.15)';" onmouseleave="this.style.transform=''; this.style.boxShadow='';" onclick="DyUserPage.downloadMix('${mixId}', '${title.replace(/'/g, "\\'")}')">
                <div style="position: relative; padding-top: 56.25%; background: var(--bg-body);">
                    <img src="${cover}" alt="${title}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;">
                    <div style="position: absolute; top: 8px; right: 8px; background: var(--primary); color: white; padding: 4px 8px; border-radius: 4px; font-size: 0.75rem; z-index: 2; font-weight: 600;">
                        合集 | 共 ${count} 个作品
                    </div>
                </div>
                <div style="padding: var(--spacing-md); display: flex; flex-direction: column; justify-content: space-between; height: 110px;">
                    <h3 style="font-size: 0.95rem; margin-bottom: 8px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.4; color: #ffffff;">${title}</h3>
                    <button class="btn btn-primary btn-sm" onclick="event.stopPropagation(); DyUserPage.downloadMix('${mixId}', '${title.replace(/'/g, "\\'")}')" style="width: 100%;">下载此合集</button>
                </div>
            </div>
        `;
    },

    async downloadMix(mixId, mixName) {
        try {
            Toast.show(`正在启动合集「${mixName}」的下载...`, 'info');
            const url = `https://www.douyin.com/collection/${mixId}`;
            const res = await fetch('/api/douyin/download-single', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            Toast.show('合集下载任务已成功启动，可在解析下载页查看进度！', 'success');
            Router.navigate('dy_parse');
        } catch (err) {
            Toast.show('启动下载失败: ' + err.message, 'error');
        }
    },

    renderVideoCard(video) {
        const title = video.desc || '无标题';
        const cover = video.video?.cover?.url_list?.[0] || '';
        const awemeId = video.aweme_id;
        const likes = this.formatNumber(video.statistics?.digg_count || 0);
        const comments = this.formatNumber(video.statistics?.comment_count || 0);

        return `
            <div class="video-card" style="border-radius: 12px; overflow: hidden; background: var(--bg-secondary); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer;" onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='0 8px 24px rgba(0,0,0,0.15)';" onmouseleave="this.style.transform=''; this.style.boxShadow='';" onclick="DyUserPage.handleCardClick(event, '${awemeId}')">
                <div style="position: relative; padding-top: 56.25%; background: var(--bg-body);">
                    <img src="${cover}" alt="${title}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;">
                    <input type="checkbox" id="dy-user-check-${awemeId}" class="dy-user-checkbox" style="position: absolute; top: 8px; left: 8px; width: 18px; height: 18px; cursor: pointer; z-index: 5; display: ${this.isSelectMode ? 'block' : 'none'};" onclick="event.stopPropagation(); DyUserPage.updateDownloadButton();" />
                    <div style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.6); color: white; padding: 4px 8px; border-radius: 4px; font-size: 0.75rem; z-index: 2;">
                        ${this.formatDuration(video.video?.duration || 0)}
                    </div>
                </div>
                <div style="padding: var(--spacing-md);">
                    <h3 style="font-size: 0.95rem; margin-bottom: 8px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; line-height: 1.4; color: #ffffff;">${title}</h3>
                    <div style="display: flex; gap: var(--spacing-md); font-size: 0.85rem; color: var(--text-muted); margin-bottom: 12px;">
                        <span>❤️ ${likes}</span>
                        <span>💬 ${comments}</span>
                    </div>
                    <button class="btn btn-primary btn-sm" onclick="DyUserPage.downloadVideo('${awemeId}')" style="width: 100%;">下载视频</button>
                </div>
            </div>
        `;
    },

    async downloadVideo(awemeId) {
        const videoObj = this.videos.find(v => v.aweme_id === awemeId);
        if (!videoObj) {
            Toast.show('找不到该视频的数据', 'error');
            return;
        }
        try {
            Toast.show('已加入下载队列...', 'info');
            const sourceName = this.user ? `${this.user.nickname}的${this.getTabLabel(this.currentTab)}` : '';
            const res = await fetch('/api/douyin/download-batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ items: [videoObj], source_name: sourceName })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            Toast.show('下载已在后台启动！', 'success');
            Router.navigate('dy_parse');
        } catch (err) {
            Toast.show(err.message, 'error');
        }
    },

    async downloadAll() {
        if (!this.user) return;

        const btn = document.getElementById('dy-user-download-btn');
        btn.disabled = true;
        btn.textContent = '正在启动...';

        try {
            const res = await fetch('/api/douyin/download-user', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    sec_uid: this.secUid,
                    types: [this.currentTab],
                    max_pages: 10
                })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            Toast.show('批量下载已启动！', 'success');
            this.updateDownloadAllButton('running');
            Router.navigate('dy_parse'); // 跳转到下载进度页面
        } catch (err) {
            Toast.show(err.message, 'error');
            this.updateDownloadAllButton('failed');
        }
    },

    async cancelDownloadAll() {
        const btn = document.getElementById('dy-user-download-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = '正在取消...';
        }
        try {
            const res = await fetch('/api/douyin/cancel-download', {
                method: 'POST'
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            Toast.show(data.message, 'info');
            this.updateDownloadAllButton('cancelled');
        } catch (err) {
            Toast.show(err.message, 'error');
            if (btn) btn.disabled = false;
        }
    },

    getTabLabel(tab) {
        const labels = {
            'post': '作品',
            'like': '喜欢',
            'collect': '收藏',
            'story': '日常',
            'mix': '合集'
        };
        return labels[tab] || '作品';
    },

    updateDownloadAllButton(status) {
        const btn = document.getElementById('dy-user-download-btn');
        if (!btn) return;

        const label = this.getTabLabel(this.currentTab);

        if (status === 'running') {
            btn.className = "btn btn-error";
            btn.onclick = () => DyUserPage.cancelDownloadAll();
            btn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; color: var(--error);">
                    <rect x="4" y="4" width="16" height="16" rx="2" fill="currentColor"/>
                </svg>
                取消批量下载
            `;
            btn.disabled = false;
        } else {
            btn.className = "btn btn-primary";
            btn.onclick = () => DyUserPage.downloadAll();
            btn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px;">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                </svg>
                批量下载全部${label}
            `;
            btn.disabled = false;
        }
    },

    handleCardClick(event, awemeId) {
        if (event.target.tagName === 'BUTTON' || event.target.tagName === 'INPUT') return;
        if (this.isSelectMode) {
            this.toggleSelection(awemeId);
        } else {
            window.open(`https://www.douyin.com/video/${awemeId}`, '_blank');
        }
    },

    enterSelectMode() {
        this.isSelectMode = true;
        document.querySelectorAll('.dy-user-checkbox').forEach(cb => cb.style.display = 'block');
        this.updateHeaderActions();
        this.updateDownloadButton();
    },

    exitSelectMode() {
        this.isSelectMode = false;
        document.querySelectorAll('.dy-user-checkbox').forEach(cb => {
            cb.checked = false;
            cb.style.display = 'none';
        });
        this.updateHeaderActions();
    },

    toggleSelectAll() {
        const checkboxes = document.querySelectorAll('.dy-user-checkbox');
        const selected = this.getSelectedVideos();
        const shouldSelectAll = selected.length < checkboxes.length;

        checkboxes.forEach(cb => cb.checked = shouldSelectAll);
        this.updateDownloadButton();
    },

    toggleSelection(awemeId) {
        const checkbox = document.getElementById(`dy-user-check-${awemeId}`);
        if (checkbox) {
            checkbox.checked = !checkbox.checked;
            this.updateDownloadButton();
        }
    },

    getSelectedVideos() {
        const selected = [];
        document.querySelectorAll('.dy-user-checkbox').forEach(cb => {
            if (cb.checked) {
                const awemeId = cb.id.replace('dy-user-check-', '');
                const videoObj = this.videos.find(v => v.aweme_id === awemeId);
                if (videoObj) {
                    selected.push(videoObj);
                }
            }
        });
        return selected;
    },

    updateHeaderActions() {
        const container = document.getElementById('dy-user-header-actions');
        if (!container) return;

        if (this.isSelectMode) {
            container.innerHTML = `
                <button class="btn btn-secondary btn-sm" id="dy-user-select-all-btn" onclick="DyUserPage.toggleSelectAll()" style="padding: 6px 12px; font-size: 0.85rem;">全选</button>
                <button class="btn btn-primary" onclick="DyUserPage.downloadSelected()" id="dy-user-batch-download-btn" disabled>
                    <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    开始下载 (0)
                </button>
                <button class="btn btn-secondary" onclick="DyUserPage.exitSelectMode()">
                    取消批量下载
                </button>
            `;
        } else {
            container.innerHTML = `
                <button class="btn btn-primary" onclick="DyUserPage.enterSelectMode()">
                    <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                        <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                    批量选择下载
                </button>
            `;
        }
    },

    updateDownloadButton() {
        const downloadBtn = document.getElementById('dy-user-batch-download-btn');
        if (!downloadBtn) return;
        const selected = this.getSelectedVideos();
        downloadBtn.disabled = selected.length === 0;

        downloadBtn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" style="width: 16px; height: 16px; margin-right: 6px; display: inline-block; vertical-align: text-bottom;">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            开始下载 (${selected.length})
        `;

        const selectAllBtn = document.getElementById('dy-user-select-all-btn');
        if (selectAllBtn) {
            const checkboxes = document.querySelectorAll('.dy-user-checkbox');
            if (checkboxes.length > 0 && selected.length === checkboxes.length) {
                selectAllBtn.textContent = '取消全选';
            } else {
                selectAllBtn.textContent = '全选';
            }
        }
    },

    async downloadSelected() {
        const selected = this.getSelectedVideos();
        if (selected.length === 0) return;

        const btn = document.getElementById('dy-user-batch-download-btn');
        let originalHTML = '';
        if (btn) {
            btn.disabled = true;
            originalHTML = btn.innerHTML;
            btn.textContent = '正在启动...';
        }

        try {
            const sourceName = this.user ? `${this.user.nickname}的${this.getTabLabel(this.currentTab)}` : '';
            const res = await fetch('/api/douyin/download-batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ items: selected, source_name: sourceName })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            Toast.show('批量下载已启动！', 'success');
            this.exitSelectMode();
            Router.navigate('dy_parse'); // 跳转到下载进度页面
        } catch (err) {
            Toast.show(err.message, 'error');
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = originalHTML;
            }
        }
    },

    showLoading() {
        document.getElementById('dy-user-loading').style.display = 'block';
        document.getElementById('dy-user-content').style.display = 'none';
        document.getElementById('dy-user-empty').style.display = 'none';
    },

    hideLoading() {
        document.getElementById('dy-user-loading').style.display = 'none';
    },

    showEmpty() {
        document.getElementById('dy-user-empty').style.display = 'block';
        document.getElementById('dy-user-content').style.display = 'none';
    },

    formatNumber(num) {
        if (num >= 10000) {
            return (num / 10000).toFixed(1) + 'w';
        }
        return num.toString();
    },

    formatDuration(ms) {
        let seconds = Math.floor((ms || 0) / 1000);
        const hrs = Math.floor(seconds / 3600);
        seconds = seconds % 3600;
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;

        if (hrs > 0) {
            return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        }
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    },

    destroy() {
        this.user = null;
        this.videos = [];
        this.cursor = 0;
        this.hasMore = false;
        this.secUid = '';
        this.loadingMore = false;
    }
};
