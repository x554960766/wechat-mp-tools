const DySearchPage = {
    cachedUsers: {},
    _favListenerAdded: false,

    render() {
        return `
            <div class="page-header">
                <h2 class="page-title">搜索用户</h2>
                <p class="page-description">通过关键词或抖音号查询创作者并进入主页</p>
            </div>
            
            <div class="card" style="margin-bottom: var(--spacing-lg);">
                <div class="search-box" style="max-width: 100%; display: flex; gap: var(--spacing-md);">
                    <div style="position: relative; flex: 1;">
                        <svg class="search-icon" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/><line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" stroke-width="2"/></svg>
                        <input type="text" id="dy-search-input" class="form-input" placeholder="输入用户名、抖音号或主页链接..." style="width: 100%;" onkeydown="if(event.key === 'Enter') DySearchPage.doSearch()">
                    </div>
                    <button class="btn btn-primary" onclick="DySearchPage.doSearch()" id="dy-search-btn">搜索</button>
                </div>
            </div>

            <div id="dy-search-results" class="card-grid" style="display: none;"></div>
            
            <div id="dy-search-empty" class="empty-state">
                <svg class="empty-state-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <div class="empty-state-title">暂无数据</div>
                <div class="empty-state-desc">请输入关键词进行搜索</div>
            </div>
        `;
    },

    async init() {
        const input = document.getElementById('dy-search-input');
        if (input) input.focus();

        if (!this._favListenerAdded) {
            this._favListenerAdded = true;
            window.addEventListener('dy-fav-changed', (e) => {
                if (e.detail && e.detail.secUid) {
                    const isFav = this.isFavoriteAuthor(e.detail.secUid);
                    this.updateCardFavButton(e.detail.secUid, isFav);
                }
            });
        }
    },

    onShow() {
        // 刷新当前所有已渲染卡片的收藏状态
        Object.keys(this.cachedUsers).forEach(secUid => {
            this.updateCardFavButton(secUid, this.isFavoriteAuthor(secUid));
        });
    },

    async doSearch() {
        const keyword = document.getElementById('dy-search-input').value.trim();
        if (!keyword) {
            Toast.show('请输入搜索内容', 'warning');
            return;
        }

        const btn = document.getElementById('dy-search-btn');
        btn.disabled = true;
        btn.textContent = '搜索中...';

        try {
            const res = await fetch(`/api/douyin/search?keyword=${encodeURIComponent(keyword)}`);
            const data = await res.json();
            
            if (data.error) throw new Error(data.error);

            this.renderResults(data);
        } catch (err) {
            Toast.show(err.message, 'error');
        } finally {
            btn.disabled = false;
            btn.textContent = '搜索';
        }
    },

    formatNumber(num) {
        if (!num || isNaN(num)) return '0';
        num = Number(num);
        if (num >= 10000) {
            return (num / 10000).toFixed(1) + 'w';
        } else if (num >= 1000) {
            return (num / 1000).toFixed(1) + 'k';
        }
        return num.toString();
    },

    renderResults(data) {
        const container = document.getElementById('dy-search-results');
        const empty = document.getElementById('dy-search-empty');
        
        container.innerHTML = '';
        this.cachedUsers = {};
        
        const users = (data.user_list || []).map(item => {
            const info = item.user_info || item;
            return {
                ...item,
                ...info,
                follower_count: info.follower_count ?? item.follower_count ?? 0,
                total_favorited: info.total_favorited ?? item.total_favorited ?? 0,
            };
        });
        
        if (users.length === 0) {
            container.style.display = 'none';
            empty.style.display = 'block';
            empty.querySelector('.empty-state-desc').textContent = '未找到相关用户，请更换关键词。注意：搜索功能可能需要登录。';
            return;
        }

        empty.style.display = 'none';
        container.style.display = 'grid';

        users.forEach(user => {
            const avatar = (user.avatar_thumb && user.avatar_thumb.url_list && user.avatar_thumb.url_list[0]) || '';
            const nickname = user.nickname || '未知用户';
            const signature = user.signature || '暂无签名';
            const sec_uid = user.sec_uid;
            const douyinId = user.unique_id || user.short_id || '';
            const isFav = this.isFavoriteAuthor(sec_uid);

            this.cachedUsers[sec_uid] = user;
            
            const card = document.createElement('div');
            card.className = 'card';
            card.style.cssText = 'cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; padding: var(--spacing-lg);';
            card.onmouseenter = () => { card.style.transform = 'translateY(-3px)'; card.style.boxShadow = '0 8px 24px rgba(0,0,0,0.15)'; };
            card.onmouseleave = () => { card.style.transform = ''; card.style.boxShadow = ''; };
            card.onclick = () => {
                window.location.hash = `#dy_user?sec_uid=${sec_uid}`;
            };
            
            card.innerHTML = `
                <div style="display: flex; gap: 16px; align-items: flex-start;">
                    <img src="${avatar}" style="width: 56px; height: 56px; border-radius: 50%; object-fit: cover; background: var(--bg-input); flex-shrink: 0;">
                    <div style="flex: 1; min-width: 0;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; margin: 0 0 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--text-primary);">${nickname}</h3>
                        ${douyinId ? `<div style="font-size: 0.78rem; color: var(--primary); margin-bottom: 6px;">抖音号: ${douyinId}</div>` : ''}
                        <p style="font-size: 0.82rem; color: var(--text-muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; margin: 0; line-height: 1.4;">${signature}</p>
                    </div>
                </div>
                <div style="display: flex; gap: 8px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border-color); align-items: center;">
                    <div style="text-align: center; min-width: 48px;">
                        <div style="font-size: 0.95rem; font-weight: 600; color: var(--text-primary);">${this.formatNumber(user.follower_count || 0)}</div>
                        <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 1px;">粉丝</div>
                    </div>
                    <div style="width: 1px; background: var(--border-color); height: 20px;"></div>
                    <div style="text-align: center; min-width: 48px;">
                        <div style="font-size: 0.95rem; font-weight: 600; color: var(--text-primary);">${this.formatNumber(user.total_favorited || 0)}</div>
                        <div style="font-size: 0.72rem; color: var(--text-muted); margin-top: 1px;">获赞</div>
                    </div>
                    <div style="flex: 1; display: flex; gap: 6px; justify-content: flex-end; align-items: center;">
                        <button class="btn btn-sm btn-secondary" id="dy-search-fav-${sec_uid}" onclick="event.stopPropagation(); DySearchPage.toggleFavorite('${sec_uid}')" style="padding: 4px 10px; font-size: 0.78rem; display: inline-flex; align-items: center; gap: 4px; border-radius: 6px; ${isFav ? 'color: #f59e0b; border-color: rgba(245, 158, 11, 0.5); background: rgba(245, 158, 11, 0.12);' : ''}">
                            <span>${isFav ? '★' : '☆'}</span> <span>${isFav ? '已收藏' : '收藏'}</span>
                        </button>
                        <button class="btn btn-sm btn-primary" onclick="event.stopPropagation(); window.location.hash='#dy_user?sec_uid=${sec_uid}';" style="padding: 4px 10px; font-size: 0.78rem; border-radius: 6px;">进入主页</button>
                    </div>
                </div>
            `;
            container.appendChild(card);
        });
    },

    getFavoriteAuthors() {
        try {
            return JSON.parse(localStorage.getItem('dy_favorite_authors') || '[]');
        } catch (e) {
            return [];
        }
    },

    isFavoriteAuthor(secUid) {
        if (!secUid) return false;
        const list = this.getFavoriteAuthors();
        return list.some(item => item.sec_uid === secUid);
    },

    toggleFavorite(secUid) {
        const user = this.cachedUsers[secUid];
        let list = this.getFavoriteAuthors();
        const existingIdx = list.findIndex(item => item.sec_uid === secUid);
        let isFav = false;

        if (existingIdx >= 0) {
            list.splice(existingIdx, 1);
            Toast.show('已取消收藏该作者', 'info');
            isFav = false;
        } else {
            const avatar = (user && user.avatar_thumb && user.avatar_thumb.url_list && user.avatar_thumb.url_list[0]) || (user && user.avatar) || '';
            const nickname = (user && user.nickname) || '未知作者';
            const signature = (user && user.signature) || '';
            const uniqueId = (user && (user.unique_id || user.short_id)) || '';

            list.unshift({
                sec_uid: secUid,
                nickname: nickname,
                avatar: avatar,
                signature: signature,
                unique_id: uniqueId,
                time: Date.now()
            });
            Toast.show('⭐ 已收藏该作者！', 'success');
            isFav = true;
        }

        try {
            localStorage.setItem('dy_favorite_authors', JSON.stringify(list));
        } catch (e) {}

        this.updateCardFavButton(secUid, isFav);
        window.dispatchEvent(new CustomEvent('dy-fav-changed', { detail: { secUid, isFav } }));
    },

    updateCardFavButton(secUid, isFav) {
        const btn = document.getElementById(`dy-search-fav-${secUid}`);
        if (!btn) return;
        if (isFav) {
            btn.innerHTML = '<span>★</span> <span>已收藏</span>';
            btn.style.color = '#f59e0b';
            btn.style.borderColor = 'rgba(245, 158, 11, 0.5)';
            btn.style.background = 'rgba(245, 158, 11, 0.12)';
        } else {
            btn.innerHTML = '<span>☆</span> <span>收藏</span>';
            btn.style.color = '';
            btn.style.borderColor = '';
            btn.style.background = '';
        }
    }
};