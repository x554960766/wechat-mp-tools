/**
 * B站链接下载组件
 */
const BiliDownloadPage = {
    _pollTimer: null,

    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">B站链接下载</h2>
                    <p class="page-description">支持粘贴B站分享文本、BV/AV号、视频链接进行单链接或批量多行解析下载。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 24px; padding: 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px;">
                <h3 style="margin-bottom: 12px; font-size: 1.1rem; color: var(--text-primary);">粘贴B站视频链接</h3>
                <div style="margin-bottom: 16px;">
                    <textarea id="bili-urls-input" class="form-control" rows="6" placeholder="支持每行一个链接，例如：
https://www.bilibili.com/video/BV1G3HEz5ETU
也可以是带文字口令的分享段落：
“这个视频超赞！【精选】XXX... BV1G3HEz5ETU... 复制打开B站”" style="width: 100%; padding: 12px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary); resize: vertical; line-height: 1.5; font-size: 0.9rem;"></textarea>
                </div>
                
                <div style="display: flex; gap: 12px; flex-wrap: wrap;">
                    <button class="btn btn-primary" id="btn-bili-start-download" onclick="BiliDownloadPage.startDownload()">📥 开始批量下载</button>
                    <button class="btn btn-secondary" id="btn-bili-parse-preview" onclick="BiliDownloadPage.parsePreview()">🔍 解析首个链接预览</button>
                </div>
            </div>

            <div id="bili-download-preview-container" style="margin-bottom: 24px; display: none;"></div>
        `;
    },

    async init() {
        this.destroy();
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

    async parsePreview() {
        const text = document.getElementById('bili-urls-input').value.trim();
        const previewBtn = document.getElementById('btn-bili-parse-preview');
        const previewContainer = document.getElementById('bili-download-preview-container');
        
        if (!text) {
            Toast.error('请输入或粘贴B站链接');
            return;
        }

        if (previewBtn) {
            previewBtn.disabled = true;
            previewBtn.innerHTML = '<div class="spinner" style="width: 14px; height: 14px; border-width: 2px;"></div> 解析中...';
        }

        try {
            const res = await API.bili.detectUrl(text);
            
            previewContainer.style.display = 'block';
            
            if (res.type === 'space') {
                previewContainer.innerHTML = `
                    <div style="background: var(--bg-card); border: 1px solid var(--primary); border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 16px; box-shadow: var(--shadow-sm);">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <h4 style="margin: 0; color: var(--primary); font-size: 1rem; font-weight: 700;">🔍 链接解析: 个人空间</h4>
                            <button class="btn btn-secondary btn-sm" onclick="BiliDownloadPage.closePreview()" style="padding: 2px 8px; font-size: 0.75rem;">关闭</button>
                        </div>
                        
                        <div style="display: flex; gap: 16px; align-items: center;">
                            ${res.avatar
                                ? `<img src="${res.avatar}" alt="" style="width: 60px; height: 60px; border-radius: 50%; object-fit: cover; border: 2px solid white; box-shadow: var(--shadow-sm);" referrerpolicy="no-referrer"
                                     onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
                                   <div style="display: none; width: 60px; height: 60px; border-radius: 50%; background: var(--primary); color: white; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700; flex-shrink: 0;">${res.nickname.charAt(0)}</div>`
                                : `<div style="width: 60px; height: 60px; border-radius: 50%; background: var(--primary); color: white; display: flex; align-items: center; justify-content: center; font-size: 1.5rem; font-weight: 700; flex-shrink: 0;">${res.nickname.charAt(0)}</div>`
                            }
                            <div style="flex: 1; min-width: 0;">
                                <h4 style="margin: 0 0 4px; font-size: 1.1rem; color: var(--text-primary); font-weight: 700;">${this._esc(res.nickname)}</h4>
                                <p style="margin: 0; font-size: 0.8rem; color: var(--text-muted);">MID: ${res.id}</p>
                                <p style="margin: 4px 0 0; font-size: 0.82rem; color: var(--text-secondary);">${this._esc(res.desc || '暂无简介')}</p>
                            </div>
                        </div>
                        <div style="display: flex; justify-content: flex-end;">
                            <button class="btn btn-primary btn-sm" onclick="BiliDownloadPage.subscribeUser('${res.id}', '${this._esc(res.nickname).replace(/'/g, "\\'")}', '${(res.avatar || '').replace(/'/g, "\\'")}', '${(res.desc || '').replace(/'/g, "\\'")}')">➕ 关注此UP主</button>
                        </div>
                    </div>
                `;
            } else {
                // List selector for single video (multi-pages), bangumi, favorites, collections
                const labelText = res.type === 'bangumi' ? '番剧专区' : (res.type === 'playlist' ? '播放列表/合集' : '单个视频');
                const coverImg = res.cover || 'https://i0.hdslb.com/bfs/archive/b88d4c979d5045050f242548cdcc54d4ff78b9ec.png';
                
                previewContainer.innerHTML = `
                    <div style="background: var(--bg-card); border: 1px solid var(--primary); border-radius: 12px; padding: 20px; display: flex; flex-direction: column; gap: 16px; box-shadow: var(--shadow-sm);">
                        <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                            <h4 style="margin: 0; color: var(--primary); font-size: 1rem; font-weight: 700;">🔍 链接解析: ${labelText}</h4>
                            <button class="btn btn-secondary btn-sm" onclick="BiliDownloadPage.closePreview()" style="padding: 2px 8px; font-size: 0.75rem;">关闭</button>
                        </div>
                        
                        <div style="display: flex; gap: 16px; align-items: center; flex-wrap: wrap;">
                            <img src="${coverImg}" referrerpolicy="no-referrer" style="width: 140px; height: 90px; border-radius: 8px; object-fit: cover; border: 1px solid var(--border-color); flex-shrink: 0;" />
                            <div style="flex: 1; min-width: 200px;">
                                <h4 style="margin: 0 0 6px; font-size: 1.05rem; color: var(--text-primary); font-weight: 700; line-height: 1.4;">${this._esc(res.title)}</h4>
                                <p style="margin: 0 0 8px; font-size: 0.85rem; color: var(--text-secondary);">
                                    来源: <strong style="color:var(--text-primary);">${this._esc(res.author)}</strong> | 项目总数: ${res.page_count}
                                </p>
                            </div>
                        </div>
                        
                        <div style="max-height: 200px; overflow-y: auto; border: 1px solid var(--border-color); border-radius: 8px; background: var(--bg-tertiary); padding: 10px;">
                            <div style="display: flex; gap: 10px; margin-bottom: 8px; font-size: 0.82rem; border-bottom: 1px solid var(--border-color); padding-bottom: 6px;">
                                <a href="javascript:;" onclick="BiliDownloadPage.selectPreviewAll(true)" style="color: var(--primary); text-decoration: none; font-weight: 500;">全选</a>
                                <span style="color: var(--text-muted);">|</span>
                                <a href="javascript:;" onclick="BiliDownloadPage.selectPreviewAll(false)" style="color: var(--text-muted); text-decoration: none; font-weight: 500;">全不选</a>
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 8px;">
                                ${res.pages.map(p => `
                                    <label style="display: flex; align-items: center; gap: 8px; font-size: 0.85rem; cursor: pointer; color: var(--text-primary);">
                                        <input type="checkbox" class="bili-preview-check" checked 
                                            data-page="${p.page}" 
                                            data-bvid="${p.bvid || res.id}" 
                                            data-title="${this._esc(p.part)}" 
                                            data-cid="${p.cid || ''}"
                                            data-bangumi="${p.is_bangumi ? 'true' : 'false'}"
                                            data-bangumi-title="${this._esc(p.bangumi_title || '')}"
                                            style="accent-color: var(--primary); width: 15px; height: 15px;" />
                                        <span>P${p.page}. ${this._esc(p.part)}</span>
                                    </label>
                                `).join('')}
                            </div>
                        </div>
                        
                        <div style="display: flex; justify-content: flex-end;">
                            <button id="btn-bili-download-parsed" class="btn btn-primary" onclick="BiliDownloadPage.downloadParsedSelection()" style="font-size: 0.88rem; padding: 6px 16px;">📥 开始下载所选 (${res.pages.length}项)</button>
                        </div>
                    </div>
                `;
            }
        } catch (err) {
            Toast.error('解析失败: ' + err.message);
        } finally {
            if (previewBtn) {
                previewBtn.disabled = false;
                previewBtn.innerHTML = '🔍 解析首个链接预览';
            }
        }
    },

    closePreview() {
        const previewContainer = document.getElementById('bili-download-preview-container');
        if (previewContainer) {
            previewContainer.style.display = 'none';
            previewContainer.innerHTML = '';
        }
    },

    selectPreviewAll(checked) {
        const checkboxes = document.querySelectorAll('.bili-preview-check');
        checkboxes.forEach(cb => {
            cb.checked = !!checked;
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
                            <input type="radio" name="bili-dl-quality" value="vip" style="width: 16px; height: 16px; accent-color: var(--primary);" checked />
                            <span><strong>1080P 高码率 / 4K</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(需要登录大会员)</span></span>
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.92rem; color: var(--text-primary);">
                            <input type="radio" name="bili-dl-quality" value="1080p" style="width: 16px; height: 16px; accent-color: var(--primary);" />
                            <span><strong>1080P 普通</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(需要登录普通账号)</span></span>
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.92rem; color: var(--text-primary);">
                            <input type="radio" name="bili-dl-quality" value="720p" style="width: 16px; height: 16px; accent-color: var(--primary);" />
                            <span><strong>720P 高清</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(未登录默认)</span></span>
                        </label>
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.92rem; color: var(--text-primary);">
                            <input type="radio" name="bili-dl-quality" value="360p" style="width: 16px; height: 16px; accent-color: var(--primary);" />
                            <span><strong>360P 标清</strong> <span style="font-size: 0.78rem; color: var(--text-muted);">(省流画质)</span></span>
                        </label>
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" id="btn-bili-cancel-dl">取消</button>
                <button class="btn btn-primary" id="btn-bili-confirm-dl">确认下载</button>
            `,
            onOpen: () => {
                document.getElementById('btn-bili-confirm-dl').onclick = async () => {
                    const quality = document.querySelector('input[name="bili-dl-quality"]:checked').value;
                    
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
                document.getElementById('btn-bili-cancel-dl').onclick = () => {
                    Modal.close();
                    if (onCancel) onCancel();
                };
            },
            onClose: () => {
                if (onCancel) onCancel();
            }
        });
    },

    async downloadParsedSelection() {
        const checkboxes = document.querySelectorAll('.bili-preview-check:checked');
        if (checkboxes.length === 0) {
            Toast.error('请至少选择一个视频进行下载');
            return;
        }

        const items = [];
        checkboxes.forEach(cb => {
            const pageIdx = parseInt(cb.dataset.page);
            const bvid = cb.dataset.bvid;
            const title = cb.dataset.title;
            const cid = cb.dataset.cid || '';
            const isBangumi = cb.dataset.bangumi === 'true';
            const bangumiTitle = cb.dataset.bangumiTitle || '';
            
            items.push({
                bvid: bvid,
                page_num: pageIdx,
                title: title,
                is_bangumi: isBangumi,
                cid: cid,
                p_title: title,
                bangumi_title: bangumiTitle
            });
        });

        // Prompt quality first
        this.promptQuality(items, async (quality) => {
            const btn = document.getElementById('btn-bili-download-parsed');
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> 正在启动...';
            }

            try {
                const res = await API.bili.downloadBatch(items, quality);
                if (res.task_started) {
                    this.closePreview();
                    this.showProgressModal();
                } else {
                    throw new Error(res.error || '无法启动任务');
                }
            } catch (err) {
                Toast.error('启动下载失败: ' + err.message);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = `📥 开始下载所选 (${checkboxes.length}项)`;
                }
            }
        }, () => {
            const btn = document.getElementById('btn-bili-download-parsed');
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `📥 开始下载所选 (${checkboxes.length}项)`;
            }
        });
    },

    async subscribeUser(mid, name, avatar, desc) {
        try {
            await API.bili.addAccount({
                mid: mid,
                nickname: name,
                avatar: avatar,
                desc: desc,
                url: `https://space.bilibili.com/${mid}`
            });
            Toast.success('已添加关注UP主');
            this.closePreview();
        } catch (e) {
            Toast.error('关注失败: ' + e.message);
        }
    },

    async startDownload() {
        const text = document.getElementById('bili-urls-input').value.trim();
        if (!text) {
            Toast.error('请输入B站链接');
            return;
        }

        const btn = document.getElementById('btn-bili-start-download');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> 提交任务中...';
        }

        try {
            const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
            const items = [];
            for (let line of lines) {
                const bvMatch = line.match(/(BV[A-Za-z0-9]{10})/i);
                const avMatch = line.match(/av(\d+)/i);
                if (bvMatch) {
                    items.push({ bvid: bvMatch[1], page_num: 1, title: bvMatch[1] });
                } else if (avMatch) {
                    items.push({ bvid: `av${avMatch[1]}`, page_num: 1, title: `av${avMatch[1]}` });
                }
            }

            if (items.length === 0) {
                const detail = await API.bili.detectUrl(text);
                if (detail && detail.id) {
                    items.push({ bvid: detail.id, page_num: 1, title: detail.title });
                }
            }

            if (items.length === 0) {
                throw new Error('未能在输入文本中提取到有效的 BV/AV 号');
            }

            this.promptQuality(items, async (quality) => {
                try {
                    const res = await API.bili.downloadBatch(items, quality);
                    if (res.task_started) {
                        this.closePreview();
                        this.showProgressModal();
                    } else {
                        throw new Error(res.error || '无法启动任务');
                    }
                } catch (err) {
                    Toast.error('启动下载失败: ' + err.message);
                } finally {
                    if (btn) {
                        btn.disabled = false;
                        btn.innerHTML = '📥 开始批量下载';
                    }
                }
            }, () => {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = '📥 开始批量下载';
                }
            });
        } catch (err) {
            Toast.error('启动下载失败: ' + err.message);
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = '📥 开始批量下载';
            }
        }
    },

    showProgressModal() {
        this.clearTimer();
        let isCancelled = false;

        Modal.open({
            title: '📥 正在下载B站视频',
            preventClose: true,
            content: `
                <div style="padding: 10px 0;">
                    <p style="font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 12px; line-height: 1.5;" id="bili-dl-current-title">
                        正在读取任务队列...
                    </p>
                    <div style="background: var(--border-color); border-radius: 8px; height: 16px; overflow: hidden; margin-bottom: 12px; position: relative;">
                        <div id="bili-dl-progress-bar" style="background: var(--primary); height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.9rem; margin-bottom: 12px;">
                        <span id="bili-dl-progress-index" style="font-weight: 500; color: var(--text-primary);">第 0/0 项</span>
                        <span id="bili-dl-progress-percent" style="font-weight: 600; color: var(--primary);">0%</span>
                    </div>
                    <div style="max-height: 120px; overflow-y: auto; font-family: monospace; font-size: 0.8rem; background: var(--bg-tertiary); padding: 10px; border-radius: 8px; border: 1px solid var(--border-color); color: var(--text-secondary);" id="bili-dl-progress-logs">
                        [系统] 任务已启动，正在初始化网络连接...
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" style="background:#ff3b30;color:white;border-color:rgba(255,59,48,0.2)" onclick="BiliDownloadPage.cancelDownload()">终止任务</button>
            `,
            onClose: () => {
                isCancelled = true;
                this.clearTimer();
            }
        });

        const updateUI = (state) => {
            const bar = document.getElementById('bili-dl-progress-bar');
            const percent = document.getElementById('bili-dl-progress-percent');
            const index = document.getElementById('bili-dl-progress-index');
            const logs = document.getElementById('bili-dl-progress-logs');
            const currTitle = document.getElementById('bili-dl-current-title');

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
