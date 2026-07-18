/**
 * B站视频下载组件
 */
const BiliVideosPage = {
    _pollTimer: null,
    _accounts: [],
    _videos: [],
    _selectedMid: '',
    _selectedAccountName: '',
    _currentPage: 1,
    _totalVideos: 0,
    _multiSelect: false,
    _selectedItems: new Set(),

    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">B站视频下载</h2>
                    <p class="page-description">浏览已关注 UP 主的空间投稿视频，支持多选批量下载并生成高清视频及弹幕、字幕。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 20px; padding: 16px 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <label for="bili-up-select" style="font-weight: 600; color: var(--text-primary); min-width: 60px;">选择UP主:</label>
                    <select id="bili-up-select" class="form-control" style="padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary); min-width: 200px;" onchange="BiliVideosPage.onUpChange(this.value)">
                        <option value="">-- 请选择UP主 --</option>
                    </select>
                </div>
                
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-secondary" onclick="BiliVideosPage.toggleMultiSelect()" id="btn-bili-multi-select" style="min-width: 80px;">☑ 多选</button>
                    <button class="btn btn-secondary" onclick="BiliVideosPage.refreshVideos()">🔄 刷新列表</button>
                    <button class="btn btn-primary" id="btn-bili-download-selected" onclick="BiliVideosPage.downloadSelected()" style="display:none;" disabled>📥 下载选中 (0)</button>
                </div>
            </div>

            <div class="info-alert" id="bili-select-bar" style="background: rgba(0,122,255,0.05); border: 1px solid rgba(0,122,255,0.15); color: var(--primary); padding: 12px 16px; border-radius: 8px; font-size: 0.88rem; margin-bottom: 20px; display: none; align-items: center; justify-content: space-between;">
                <span>💡 <strong>操作：</strong>勾选下方的视频，点击右侧的下载按钮即可批量后台下载。</span>
                <div style="display: flex; gap: 12px;">
                    <span onclick="BiliVideosPage.selectAll()" style="cursor: pointer; font-weight: 600; text-decoration: underline;">全选本页</span>
                    <span onclick="BiliVideosPage.deselectAll()" style="cursor: pointer; font-weight: 600; text-decoration: underline;">取消全选</span>
                </div>
            </div>

            <div id="bili-videos-grid" class="animate-fade-in" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; margin-bottom: 30px;">
                <div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 60px 24px;">请先选择UP主</div>
            </div>

            <!-- 分页控制器 -->
            <div id="bili-videos-pagination" style="display: flex; justify-content: center; align-items: center; gap: 16px; margin-bottom: 40px; display: none;">
                <button class="btn btn-secondary btn-sm" id="btn-bili-prev-page" onclick="BiliVideosPage.changePage(-1)">◀ 上一页</button>
                <span id="bili-page-display" style="font-weight: 600; color: var(--text-primary); font-size: 0.95rem;">第 1 页</span>
                <button class="btn btn-secondary btn-sm" id="btn-bili-next-page" onclick="BiliVideosPage.changePage(1)">下一页 ▶</button>
            </div>
        `;
    },

    async init(params) {
        this.destroy();
        this._selectedItems.clear();
        this._currentPage = 1;
        this._multiSelect = false;
        
        try {
            const data = await API.bili.listAccounts();
            this._accounts = data.accounts || [];
            
            const select = document.getElementById('bili-up-select');
            if (select) {
                select.innerHTML = '<option value="">-- 请选择UP主 --</option>' + 
                    this._accounts.map(acc => `<option value="${acc.mid}">${this._esc(acc.nickname)}</option>`).join('');
                
                if (params && params.mid) {
                    select.value = params.mid;
                    this.onUpChange(params.mid);
                }
            }
        } catch (err) {
            Toast.error('获取UP主列表失败: ' + err.message);
        }
    },

    destroy() {
        this.clearTimer();
    },

    clearTimer() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async onUpChange(mid) {
        this._selectedMid = mid;
        this._currentPage = 1;
        this._selectedItems.clear();
        this.updateDownloadBtn();
        
        const grid = document.getElementById('bili-videos-grid');
        const pager = document.getElementById('bili-videos-pagination');
        
        if (!mid) {
            this._selectedAccountName = '';
            this._videos = [];
            this._totalVideos = 0;
            if (grid) grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 60px 24px;">请先选择UP主</div>`;
            if (pager) pager.style.display = 'none';
            return;
        }

        const up = this._accounts.find(a => a.mid === mid);
        this._selectedAccountName = up ? up.nickname : 'unknown';

        await this.loadVideos();
    },

    async loadVideos() {
        const grid = document.getElementById('bili-videos-grid');
        const pager = document.getElementById('bili-videos-pagination');
        
        if (grid) {
            grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 60px;"><div class="spinner" style="margin: 0 auto;"></div><p style="margin-top:12px;color:var(--text-muted);">正在获取视频投稿列表...</p></div>';
        }

        try {
            const res = await API.bili.listVideos(this._selectedMid, this._currentPage);
            const videos = res.videos || [];
            this._videos = videos;
            this._totalVideos = res.total || 0;

            this.renderVideos();
            
            if (pager) {
                pager.style.display = this._totalVideos > 0 ? 'flex' : 'none';
                const pageDisplay = document.getElementById('bili-page-display');
                if (pageDisplay) {
                    const totalPages = Math.ceil(this._totalVideos / 30) || 1;
                    pageDisplay.textContent = `第 ${this._currentPage} / ${totalPages} 页 (共 ${this._totalVideos} 个视频)`;
                }
                
                // Disable/enable buttons
                const prevBtn = document.getElementById('btn-bili-prev-page');
                const nextBtn = document.getElementById('btn-bili-next-page');
                if (prevBtn) prevBtn.disabled = this._currentPage <= 1;
                if (nextBtn) {
                    const totalPages = Math.ceil(this._totalVideos / 30) || 1;
                    nextBtn.disabled = this._currentPage >= totalPages;
                }
            }
        } catch (err) {
            if (grid) {
                grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--error); padding: 60px 24px;">获取投稿视频失败: ${this._esc(err.message)}</div>`;
            }
            if (pager) pager.style.display = 'none';
        }
    },

    async refreshVideos() {
        if (!this._selectedMid) {
            Toast.warning('请先选择UP主');
            return;
        }
        await this.loadVideos();
    },

    changePage(delta) {
        const totalPages = Math.ceil(this._totalVideos / 30) || 1;
        const newPage = this._currentPage + delta;
        if (newPage >= 1 && newPage <= totalPages) {
            this._currentPage = newPage;
            this.loadVideos();
        }
    },

    renderVideos() {
        const grid = document.getElementById('bili-videos-grid');
        if (!grid) return;

        if (this._videos.length === 0) {
            grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 60px 24px;">该UP主暂无公开视频投稿</div>`;
            return;
        }

        const isMulti = this._multiSelect;

        grid.innerHTML = this._videos.map((vid, idx) => {
            const bvid = vid.bvid;
            const title = vid.title ? this._esc(vid.title) : '无标题视频';
            const play = vid.play || 0;
            const length = vid.length || '00:00';
            
            // Format created date
            let dateStr = '未知时间';
            if (vid.created) {
                const date = new Date(vid.created * 1000);
                dateStr = `${date.getFullYear()}-${(date.getMonth()+1).toString().padStart(2,'0')}-${date.getDate().toString().padStart(2,'0')}`;
            }

            // Cover referer bypass
            const coverUrl = vid.pic ? (vid.pic.startsWith('http') ? vid.pic : 'https:' + vid.pic) : '';

            const isChecked = this._selectedItems.has(bvid);

            const cardClick = isMulti
                ? `BiliVideosPage.toggleSelection('${bvid}')`
                : `BiliVideosPage.openDetailModal('${bvid}')`;

            const cornerControl = isMulti
                ? `<input type="checkbox" id="bili-video-check-${bvid}" ${isChecked ? 'checked' : ''} style="position: absolute; top: 8px; right: 8px; width: 18px; height: 18px; cursor: pointer; pointer-events: none;" />`
                : '';

            const downloadBtn = `
                <button class="btn btn-secondary" style="padding: 2px 8px; font-size: 0.7rem; border-radius: 4px; min-width: auto; background: var(--primary); border-color: var(--primary); color: white;" 
                    onclick="event.stopPropagation(); BiliVideosPage.downloadSingle('${bvid}', '${title.replace(/'/g, "\\'")}')">📥 下载</button>
            `;

            return `
                <div style="
                    background: var(--bg-card);
                    border: 1px solid var(--border-color);
                    border-radius: 10px;
                    overflow: hidden;
                    display: flex;
                    flex-direction: column;
                    position: relative;
                    cursor: pointer;
                    transition: transform 0.2s, box-shadow 0.2s;
                " onclick="${cardClick}"
                  onmouseenter="this.style.transform='scale(1.02)'; this.style.boxShadow='var(--shadow-md)';"
                  onmouseleave="this.style.transform='none'; this.style.boxShadow='none';">

                    <div style="width: 100%; padding-top: 56.25%; background: var(--bg-tertiary); position: relative; overflow: hidden;">
                        <img src="${coverUrl}" style="position: absolute; top:0; left:0; width:100%; height:100%; object-fit: cover;" referrerpolicy="no-referrer" onerror="this.onerror=null; this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'100\\' height=\\'100\\' viewBox=\\'0 0 100 100\\'><rect width=\\'100%\\' height=\\'100%\\' fill=\\'%23eee\\'/><text x=\\'50%\\' y=\\'50%\\' font-size=\\'12\\' text-anchor=\\'middle\\' alignment-baseline=\\'middle\\' fill=\\'%23999\\'>暂无封面</text></svg>'" />
                        <span style="position: absolute; bottom: 6px; right: 6px; background: rgba(0,0,0,0.65); color: white; padding: 1px 4px; border-radius: 4px; font-size: 0.7rem; font-weight: 600;">
                            ${length}
                        </span>
                        ${cornerControl}
                    </div>

                    <div style="padding: 10px; flex: 1; display: flex; flex-direction: column; justify-content: space-between;">
                        <div style="font-size: 0.85rem; font-weight: 600; color: var(--text-primary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; height: 32px; margin-bottom: 8px; line-height: 1.25; word-break: break-all;" title="${title}">
                            ${title}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; color: var(--text-muted);">
                            <span>📅 ${dateStr}</span>
                            ${downloadBtn}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    },

    toggleMultiSelect() {
        this._multiSelect = !this._multiSelect;
        const selectBar = document.getElementById('bili-select-bar');
        const mBtn = document.getElementById('btn-bili-multi-select');
        const dlBtn = document.getElementById('btn-bili-download-selected');

        if (this._multiSelect) {
            if (selectBar) selectBar.style.display = 'flex';
            if (mBtn) {
                mBtn.classList.add('btn-primary');
                mBtn.textContent = '取消多选';
            }
            if (dlBtn) dlBtn.style.display = 'inline-block';
        } else {
            this._selectedItems.clear();
            if (selectBar) selectBar.style.display = 'none';
            if (mBtn) {
                mBtn.classList.remove('btn-primary');
                mBtn.textContent = '☑ 多选';
            }
            if (dlBtn) dlBtn.style.display = 'none';
        }
        this.renderVideos();
        this.updateDownloadBtn();
    },

    toggleSelection(bvid) {
        if (this._selectedItems.has(bvid)) {
            this._selectedItems.delete(bvid);
        } else {
            this._selectedItems.add(bvid);
        }
        const checkbox = document.getElementById(`bili-video-check-${bvid}`);
        if (checkbox) checkbox.checked = this._selectedItems.has(bvid);
        this.updateDownloadBtn();
    },

    selectAll() {
        this._videos.forEach(v => {
            if (v.bvid) this._selectedItems.add(v.bvid);
        });
        this.renderVideos();
        this.updateDownloadBtn();
    },

    deselectAll() {
        this._selectedItems.clear();
        this.renderVideos();
        this.updateDownloadBtn();
    },

    updateDownloadBtn() {
        const btn = document.getElementById('btn-bili-download-selected');
        if (!btn) return;
        const count = this._selectedItems.size;
        btn.disabled = count === 0;
        btn.textContent = `📥 下载选中 (${count})`;
    },

    async openDetailModal(bvid) {
        try {
            // Preview single video info
            const detail = await API.bili.detectUrl(`https://www.bilibili.com/video/${bvid}`);
            
            // Check if there are multiple parts
            const pages = detail.pages || [];
            let partHtml = '';
            if (pages.length > 1) {
                partHtml = `
                    <div style="margin-top: 14px; text-align: left;">
                        <label class="form-label" style="font-weight:600;margin-bottom:6px;">分P选集 (多选):</label>
                        <div style="max-height: 150px; overflow-y: auto; border: 1px solid var(--border-color); padding: 8px; border-radius: 6px; background: var(--bg-tertiary);">
                            ${pages.map(p => `
                                <div style="display: flex; gap: 8px; align-items: center; padding: 4px 0;">
                                    <input type="checkbox" id="bili-part-check-${p.page}" value="${p.page}" checked style="width:15px;height:15px;" />
                                    <label for="bili-part-check-${p.page}" style="font-size:0.82rem;color:var(--text-secondary);cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">P${p.page} - ${this._esc(p.part)}</label>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                `;
            }

            Modal.open({
                title: '📺 视频详情预览',
                content: `
                    <div style="text-align: center; padding: 10px 0;">
                        <img src="${detail.cover}" referrerpolicy="no-referrer" style="width: 240px; border-radius: 8px; object-fit: cover; margin-bottom: 16px; border: 1px solid var(--border-color);" />
                        <h4 style="margin: 0 0 8px; font-weight: 700; color: var(--text-primary); text-align: left;">${this._esc(detail.title)}</h4>
                        <p style="margin: 0; color: var(--text-muted); font-size: 0.85rem; text-align: left;">作者: ${this._esc(detail.author)} | 分P总数: ${detail.page_count}</p>
                        ${partHtml}
                    </div>
                `,
                footer: `
                    <button class="btn btn-secondary" onclick="Modal.close()">取消</button>
                    <button class="btn btn-primary" onclick="BiliVideosPage.downloadFromPreview('${bvid}', ${pages.length})">📥 立即下载</button>
                `
            });
        } catch (err) {
            Toast.error('获取视频详情失败: ' + err.message);
        }
    },

    async downloadFromPreview(bvid, totalPages) {
        App.ensureFFmpeg(async () => {
            const pages = [];
            if (totalPages > 1) {
                for (let i = 1; i <= totalPages; i++) {
                    const cb = document.getElementById(`bili-part-check-\${i}`);
                    if (cb && cb.checked) {
                        pages.push(i);
                    }
                }
                if (pages.length === 0) {
                    Toast.warning('请至少勾选一个分P进行下载');
                    return;
                }
            }
            
            Modal.close();
            
            try {
                await API.bili.downloadSingle(`https://www.bilibili.com/video/\${bvid}`, pages.length > 0 ? pages : null);
                Toast.success('下载任务已提交到后台');
                this.showProgressModal();
            } catch (err) {
                Toast.error('提交下载失败: ' + err.message);
            }
        });
    },

    promptQuality(items, onConfirm, onCancel) {
        Modal.open({
            title: '📺 选择下载视频清晰度',
            preventClose: false,
            content: `
                <div style="padding: 10px 0;">
                    <p style="margin-bottom: var(--spacing-md); color: var(--text-muted); font-size: 0.9rem;">请选择本次任务的下载画质：</p>
                    <div style="display: flex; flex-direction: column; gap: 12px;">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.92rem; color: var(--text-primary);">
                            <input type="radio" name="bili-video-quality" value="vip" style="width: 16px; height: 16px; accent-color: var(--primary);" checked />
                            <span><strong>1080P 高码率 / 4K</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(需要登录大会员)</span></span>
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.92rem; color: var(--text-primary);">
                            <input type="radio" name="bili-video-quality" value="1080p" style="width: 16px; height: 16px; accent-color: var(--primary);" />
                            <span><strong>1080P 普通</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(需要登录普通账号)</span></span>
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.92rem; color: var(--text-primary);">
                            <input type="radio" name="bili-video-quality" value="720p" style="width: 16px; height: 16px; accent-color: var(--primary);" />
                            <span><strong>720P 高清</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(未登录默认)</span></span>
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.92rem; color: var(--text-primary);">
                            <input type="radio" name="bili-video-quality" value="360p" style="width: 16px; height: 16px; accent-color: var(--primary);" />
                            <span><strong>360P 标清</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(省流画质)</span></span>
                        </label>
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" id="btn-bili-v-cancel-dl">取消</button>
                <button class="btn btn-primary" id="btn-bili-v-confirm-dl">确认下载</button>
            `,
            onOpen: () => {
                document.getElementById('btn-bili-v-confirm-dl').onclick = async () => {
                    const quality = document.querySelector('input[name="bili-video-quality"]:checked').value;
                    
                    if (quality === 'vip') {
                        try {
                            const loginStatus = await API.bili.auth.status();
                            if (!loginStatus.logged_in) {
                                Toast.error('下载 VIP / 4K 画质需要登录B站账号，请在右上角“账号登录”中扫码登录');
                                return;
                            }
                            if (!loginStatus.account_info || !loginStatus.account_info.vip_status) {
                                Toast.warning('提示: 您当前登录的账号不是大会员，下载 VIP 画质可能会自动降级');
                            }
                        } catch (e) {
                            console.error('Check login failed', e);
                        }
                    }
                    
                    Modal.close(true);
                    onConfirm(quality);
                };
                document.getElementById('btn-bili-v-cancel-dl').onclick = () => {
                    Modal.close();
                    if (onCancel) onCancel();
                };
            },
            onClose: () => {
                if (onCancel) onCancel();
            }
        });
    },

    async downloadSingle(bvid, title) {
        App.ensureFFmpeg(() => {
            this.promptQuality([{ bvid, title }], async (quality) => {
                try {
                    await API.bili.downloadSingle(`https://www.bilibili.com/video/\${bvid}`, null, quality);
                    Toast.success(`正在下载: \${title}`);
                    this.showProgressModal();
                } catch (err) {
                    Toast.error('提交下载失败: ' + err.message);
                }
            });
        });
    },

    async downloadSelected() {
        App.ensureFFmpeg(() => {
            const list = Array.from(this._selectedItems).map(bvid => {
                const vid = this._videos.find(v => v.bvid === bvid);
                return {
                    bvid: bvid,
                    title: vid ? vid.title : bvid,
                    page_num: 1
                };
            });

            if (list.length === 0) return;

            this.promptQuality(list, async (quality) => {
                try {
                    await API.bili.downloadBatch(list, quality);
                    Toast.success('批量下载任务已启动');
                    this.toggleMultiSelect(); // Reset multi selection
                    this.showProgressModal();
                } catch (err) {
                    Toast.error('启动批量下载失败: ' + err.message);
                }
            });
        });
    },

    showProgressModal() {
        this.clearTimer();
        let isCancelled = false;

        Modal.open({
            title: '📥 正在下载B站视频',
            preventClose: true,
            content: `
                <div style="padding: 10px 0;">
                    <p style="font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 12px; line-height: 1.5;" id="bili-progress-current-title">
                        正在读取任务队列...
                    </p>
                    <div style="background: var(--border-color); border-radius: 8px; height: 16px; overflow: hidden; margin-bottom: 12px; position: relative;">
                        <div id="bili-progress-bar" style="background: var(--primary); height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.9rem; margin-bottom: 12px;">
                        <span id="bili-progress-index" style="font-weight: 500; color: var(--text-primary);">第 0/0 项</span>
                        <span id="bili-progress-percent" style="font-weight: 600; color: var(--primary);">0%</span>
                    </div>
                    <div style="max-height: 120px; overflow-y: auto; font-family: monospace; font-size: 0.8rem; background: var(--bg-tertiary); padding: 10px; border-radius: 8px; border: 1px solid var(--border-color); color: var(--text-secondary);" id="bili-progress-logs">
                        [系统] 任务已启动，正在初始化网络连接...
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" style="background:#ff3b30;color:white;border-color:rgba(255,59,48,0.2)" onclick="BiliVideosPage.cancelDownload()">终止任务</button>
            `,
            onClose: () => {
                isCancelled = true;
                this.clearTimer();
            }
        });

        const updateUI = (state) => {
            const bar = document.getElementById('bili-progress-bar');
            const percent = document.getElementById('bili-progress-percent');
            const index = document.getElementById('bili-progress-index');
            const logs = document.getElementById('bili-progress-logs');
            const currTitle = document.getElementById('bili-progress-current-title');

            const total = state.total || 0;
            const curIdx = state.current_index || 0;
            const success = state.downloaded_count || 0;
            const failed = state.failed_count || 0;
            const currentPercent = state.current_percent || 0;
            const ratio = total > 0 ? (((curIdx - 1) + (currentPercent / 100)) / total) * 100 : 0;
            
            if (bar) bar.style.width = `${ratio}%`;
            if (percent) percent.textContent = `${Math.round(ratio)}%`;
            if (index) index.textContent = `第 ${curIdx}/${total} 项 (成功 ${success} | 失败 ${failed})`;
            if (currTitle && state.current_title) {
                currTitle.innerHTML = `正在下载: <strong style="color:var(--text-primary);">${this._esc(state.current_title)}</strong>`;
            }
            if (logs && state.logs) {
                logs.innerHTML = state.logs.map(log => `<div>${this._esc(log)}</div>`).join('');
                logs.scrollTop = logs.scrollHeight;
            }
        };

        this._pollTimer = setInterval(async () => {
            if (isCancelled) return;
            try {
                const state = await API.bili.progress();
                updateUI(state);

                if (state.status === 'completed' || state.status === 'failed' || state.status === 'cancelled') {
                    this.clearTimer();
                    setTimeout(() => {
                        Modal.close();
                        if (state.status === 'completed') {
                            Toast.success('所有下载任务已全部完成！');
                        } else if (state.status === 'cancelled') {
                            Toast.info('下载任务已被用户终止');
                        } else {
                            Toast.error('下载任务失败，请检查网络或 Cookie');
                        }
                    }, 1000);
                }
            } catch (err) {
                // Ignore
            }
        }, 1500);
    },

    async cancelDownload() {
        try {
            await API.bili.cancelDownload();
            Toast.show('已提交取消请求', 'info');
        } catch (err) {
            Toast.error('取消任务失败: ' + err.message);
        }
    },

    _esc(s) {
        if (!s) return '';
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }
};
