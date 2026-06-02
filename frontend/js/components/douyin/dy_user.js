const DyUserPage = {
    user: null,
    videos: [],
    loading: false,
    cursor: 0,
    hasMore: false,
    secUid: '',
    loadingMore: false,

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
                        <h3 style="margin-bottom: var(--spacing-md);">作品列表</h3>
                        <div id="dy-user-videos" class="video-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--spacing-md);"></div>
                        <div id="dy-user-videos-empty" style="display: none; text-align: center; padding: var(--spacing-xl); color: var(--text-muted);">
                            暂无作品
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

        if (secUid) {
            await this.loadUser(secUid);
        }
    },

    async loadUser(secUid) {
        this.loading = true;
        this.showLoading();
        this.secUid = secUid;
        this.cursor = 0;
        this.hasMore = false;
        this.videos = [];

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
            const res = await fetch(`/api/douyin/user-videos?sec_uid=${this.secUid}&max_cursor=${this.cursor}&count=18`);
            const data = await res.json();

            if (data.error) {
                throw new Error(data.error);
            }

            const newVideos = data.aweme_list || [];
            this.videos = this.videos.concat(newVideos);
            this.cursor = data.max_cursor || 0;
            this.hasMore = data.has_more || false;

            this.renderVideos();
        } catch (err) {
            console.error('加载作品失败:', err);
            Toast.show('加载作品失败: ' + err.message, 'error');
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
    },

    renderVideos() {
        const container = document.getElementById('dy-user-videos');
        const empty = document.getElementById('dy-user-videos-empty');
        const moreContainer = document.getElementById('dy-user-more-container');

        if (this.videos.length === 0) {
            container.style.display = 'none';
            empty.style.display = 'block';
            if (moreContainer) moreContainer.style.display = 'none';
            return;
        }

        empty.style.display = 'none';
        container.style.display = 'grid';

        container.innerHTML = this.videos.map(video => this.renderVideoCard(video)).join('');

        if (moreContainer) {
            moreContainer.style.display = this.hasMore ? 'block' : 'none';
        }
    },

    renderVideoCard(video) {
        const title = video.desc || '无标题';
        const cover = video.video?.cover?.url_list?.[0] || '';
        const awemeId = video.aweme_id;
        const likes = this.formatNumber(video.statistics?.digg_count || 0);
        const comments = this.formatNumber(video.statistics?.comment_count || 0);

        return `
            <div class="video-card" style="border-radius: 12px; overflow: hidden; background: var(--bg-secondary); transition: transform 0.3s, box-shadow 0.3s; cursor: pointer;" onmouseenter="this.style.transform='translateY(-4px)'; this.style.boxShadow='0 8px 24px rgba(0,0,0,0.15)';" onmouseleave="this.style.transform=''; this.style.boxShadow='';" onclick="if(event.target.tagName !== 'BUTTON') window.open('https://www.douyin.com/video/${awemeId}', '_blank')">
                <div style="position: relative; padding-top: 56.25%; background: var(--bg-body);">
                    <img src="${cover}" alt="${title}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: cover;">
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
        try {
            Toast.show('开始下载...', 'info');
            const res = await fetch('/api/douyin/download-single', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url: awemeId })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            Toast.show('下载完成！', 'success');
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
            const url = `https://www.douyin.com/user/${this.user.sec_uid}`;
            const res = await fetch('/api/douyin/download-profile', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ url, max_pages: 10 })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            Toast.show('批量下载已启动！', 'success');
            Router.navigate('dy_parse'); // 跳转到下载进度页面
        } catch (err) {
            Toast.show(err.message, 'error');
            btn.disabled = false;
            btn.textContent = '批量下载全部作品';
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

    destroy() {
        this.user = null;
        this.videos = [];
        this.cursor = 0;
        this.hasMore = false;
        this.secUid = '';
        this.loadingMore = false;
    }
};
