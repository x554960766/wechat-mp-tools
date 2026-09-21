const DySearchPage = {
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

            <!-- ⭐ 收藏作者快捷访问面板 -->
            <div id="dy-fav-authors-box" class="card" style="margin-bottom: var(--spacing-lg); display: none;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div style="font-weight: 600; font-size: 0.95rem; color: var(--text-primary); display: flex; align-items: center; gap: 6px;">
                        <span>⭐</span> <span>我的收藏作者</span>
                    </div>
                    <span id="dy-fav-authors-count" style="font-size: 0.8rem; color: var(--text-muted);">已收藏 0 位</span>
                </div>
                <div id="dy-fav-authors-list" style="display: flex; gap: 12px; overflow-x: auto; padding-bottom: 4px; flex-wrap: wrap;"></div>
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
        this.renderFavoriteAuthors();
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
            
            const card = document.createElement('div');
            card.className = 'card';
            card.style.cssText = 'cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; padding: var(--spacing-lg);';
            card.onmouseenter = () => { card.style.transform = 'translateY(-3px)'; card.style.boxShadow = '0 8px 24px rgba(0,0,0,0.15)'; };
            card.onmouseleave = () => { card.style.transform = ''; card.style.boxShadow = ''; };
            card.onclick = () => {
                // Navigate to user detail page and pass sec_uid via hash params or global state
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
                <div style="display: flex; gap: 12px; margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border-color); align-items: center;">
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary);">${this.formatNumber(user.follower_count || 0)}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">粉丝</div>
                    </div>
                    <div style="width: 1px; background: var(--border-color); height: 24px;"></div>
                    <div style="flex: 1; text-align: center;">
                        <div style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary);">${this.formatNumber(user.total_favorited || 0)}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">获赞</div>
                    </div>
                    <div style="width: 1px; background: var(--border-color); height: 24px;"></div>
                    <div style="flex: 1; text-align: center;">
                        <span class="btn btn-sm btn-primary" style="padding: 4px 10px; font-size: 0.75rem; pointer-events: none;">进入主页</span>
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

    renderFavoriteAuthors() {
        const box = document.getElementById('dy-fav-authors-box');
        const listEl = document.getElementById('dy-fav-authors-list');
        const countEl = document.getElementById('dy-fav-authors-count');
        if (!box || !listEl) return;

        const list = this.getFavoriteAuthors();
        if (list.length === 0) {
            box.style.display = 'none';
            return;
        }

        box.style.display = 'block';
        if (countEl) countEl.textContent = `已收藏 ${list.length} 位`;

        listEl.innerHTML = list.map(author => {
            const avatar = author.avatar || 'data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%2244%22 height=%2244%22%3E%3Ccircle fill=%22%23444%22 cx=%2222%22 cy=%2222%22 r=%2222%22/%3E%3C/svg%3E';
            const name = author.nickname || '未知作者';
            const secUid = author.sec_uid || '';
            return `
                <div class="fav-author-chip" onclick="window.location.hash='#dy_user?sec_uid=${encodeURIComponent(secUid)}'" style="position: relative; display: flex; align-items: center; gap: 10px; padding: 6px 14px 6px 8px; background: var(--bg-input); border-radius: 30px; border: 1px solid var(--border-color); cursor: pointer; transition: all 0.2s;" onmouseenter="this.style.borderColor='var(--primary)'; this.style.transform='translateY(-2px)';" onmouseleave="this.style.borderColor='var(--border-color)'; this.style.transform='';">
                    <img src="${avatar}" style="width: 32px; height: 32px; border-radius: 50%; object-fit: cover; background: #222;">
                    <div style="font-size: 0.88rem; font-weight: 500; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary);">${name}</div>
                    <span onclick="event.stopPropagation(); DySearchPage.removeFavoriteAuthor('${secUid}')" title="取消收藏" style="color: var(--text-muted); font-size: 1.1rem; line-height: 1; padding: 0 2px; margin-left: 2px; cursor: pointer;" onmouseenter="this.style.color='#ef4444'" onmouseleave="this.style.color='var(--text-muted)'">×</span>
                </div>
            `;
        }).join('');
    },

    removeFavoriteAuthor(secUid) {
        const list = this.getFavoriteAuthors().filter(item => item.sec_uid !== secUid);
        try {
            localStorage.setItem('dy_favorite_authors', JSON.stringify(list));
        } catch (e) {}
        this.renderFavoriteAuthors();
        Toast.show('已移除该收藏作者', 'info');
    }
};