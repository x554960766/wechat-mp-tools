/**
 * 小红书笔记下载组件
 */
const XhsNotesPage = {
    _pollTimer: null,
    _accounts: [],
    _notes: [],
    _warning: null,
    _selectedUserId: '',
    _selectedAccountName: '',

    render() {
        return `
            <div class="page-header">
                <div>
                    <h2 class="page-title">小红书笔记下载</h2>
                    <p class="page-description">选择收藏的博主，浏览其主页首屏笔记，勾选进行批量下载。</p>
                </div>
            </div>

            <div class="card" style="margin-bottom: 20px; padding: 16px 20px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 16px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <label for="xhs-blogger-select" style="font-weight: 600; color: var(--text-primary); min-width: 60px;">选择博主:</label>
                    <select id="xhs-blogger-select" class="form-control" style="padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border-color); background: var(--bg-input); color: var(--text-primary); min-width: 200px;" onchange="XhsNotesPage.onBloggerChange(this.value)">
                        <option value="">-- 请选择博主 --</option>
                    </select>
                </div>
                
                <div style="display: flex; gap: 10px;">
                    <button class="btn btn-secondary" onclick="XhsNotesPage.refreshNotes()">🔄 刷新列表</button>
                    <button class="btn btn-primary" id="btn-xhs-download-selected" onclick="XhsNotesPage.downloadSelected()" disabled>📥 下载选中 (0)</button>
                </div>
            </div>

            <div class="info-alert" style="background: rgba(0,122,255,0.05); border: 1px solid rgba(0,122,255,0.15); color: var(--primary); padding: 12px 16px; border-radius: 8px; font-size: 0.88rem; margin-bottom: 20px; display: flex; align-items: center; justify-content: space-between;">
                <span>💡 <strong>提示：</strong>由于小红书风控签名限制，当前仅能获取博主主页首屏约 30 条笔记。</span>
                <div style="display: flex; gap: 12px;">
                    <span onclick="XhsNotesPage.selectAll()" style="cursor: pointer; font-weight: 600; text-decoration: underline;">全选</span>
                    <span onclick="XhsNotesPage.deselectAll()" style="cursor: pointer; font-weight: 600; text-decoration: underline;">取消全选</span>
                </div>
            </div>

            <div id="xhs-notes-grid" class="animate-fade-in" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; margin-bottom: 30px;">
                <div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 60px 24px;">请先选择博主</div>
            </div>
        `;
    },

    async init(params) {
        this.destroy();
        
        try {
            const data = await API.xhs.listAccounts();
            this._accounts = data.accounts || [];
            
            const select = document.getElementById('xhs-blogger-select');
            if (select) {
                select.innerHTML = '<option value="">-- 请选择博主 --</option>' + 
                    this._accounts.map(acc => `<option value="${acc.user_id}">${this._esc(acc.nickname)}</option>`).join('');
                
                if (params && params.user_id) {
                    select.value = params.user_id;
                    this.onBloggerChange(params.user_id);
                }
            }
        } catch (err) {
            Toast.error('获取博主列表失败: ' + err.message);
        }
    },

    destroy() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    },

    async onBloggerChange(userId) {
        this._selectedUserId = userId;
        const grid = document.getElementById('xhs-notes-grid');
        const downloadBtn = document.getElementById('btn-xhs-download-selected');
        
        if (downloadBtn) {
            downloadBtn.disabled = true;
            downloadBtn.textContent = '📥 下载选中 (0)';
        }

        if (!userId) {
            this._selectedAccountName = '';
            this._notes = [];
            if (grid) grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 60px 24px;">请先选择博主</div>`;
            return;
        }

        const blogger = this._accounts.find(a => a.user_id === userId);
        this._selectedAccountName = blogger ? blogger.nickname : 'unknown';

        if (grid) {
            grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 60px;"><div class="spinner" style="margin: 0 auto;"></div><p style="margin-top:12px;color:var(--text-muted);">正在获取博主笔记列表...</p></div>';
        }

        try {
            const res = await API.xhs.listNotes(userId);
            const notes = Array.isArray(res) ? res : (res.notes || []);
            this._notes = notes;
            this._warning = (res && !Array.isArray(res)) ? res.warning : null;
            this.renderNotes();
        } catch (err) {
            if (grid) {
                grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--error); padding: 60px 24px;">获取笔记失败: ${this._esc(err.message)}</div>`;
            }
        }
    },

    async refreshNotes() {
        if (!this._selectedUserId) {
            Toast.warning('请先选择博主');
            return;
        }
        await this.onBloggerChange(this._selectedUserId);
    },

    renderNotes() {
        const grid = document.getElementById('xhs-notes-grid');
        if (!grid) return;

        const warningBanner = this._warning
            ? `<div style="grid-column: 1/-1; background: rgba(255,149,0,0.08); border: 1px solid rgba(255,149,0,0.3); color: #b25c00; padding: 12px 16px; border-radius: 8px; font-size: 0.85rem; margin-bottom: 4px;">⚠️ ${this._esc(this._warning)}</div>`
            : '';

        if (this._notes.length === 0) {
            grid.innerHTML = warningBanner + `<div style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 60px 24px;">该博主没有笔记或获取失败</div>`;
            return;
        }

        grid.innerHTML = warningBanner + this._notes.map((note, idx) => {
            const isVideo = note.type === 'video';
            const icon = isVideo ? '🎬' : '📸';
            const typeLabel = isVideo ? '视频' : '图文';
            const title = note.title ? this._esc(note.title) : '无标题笔记';
            const hasId = !!note.note_id;
            const clickHandler = hasId ? `XhsNotesPage.toggleNoteSelection(${idx})` : `XhsNotesPage.openInBrowser()`;
            const cornerControl = hasId
                ? `<input type="checkbox" id="xhs-note-check-${idx}" style="position: absolute; top: 8px; right: 8px; width: 18px; height: 18px; cursor: pointer; pointer-events: none;" />`
                : `<span style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.6); color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.7rem;">🔗 浏览器打开</span>`;
            const footerLeft = hasId
                ? `<span>ID: ${note.note_id.substring(0, 8)}...</span>`
                : `<span style="color: var(--warning);">无ID·点击在浏览器打开</span>`;

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
                " onclick="${clickHandler}"
                  onmouseenter="this.style.transform='scale(1.02)'; this.style.boxShadow='var(--shadow-md)';"
                  onmouseleave="this.style.transform='none'; this.style.boxShadow='none';">

                    <div style="width: 100%; padding-top: 130%; background: var(--bg-tertiary); position: relative; overflow: hidden;">
                        <img src="${note.cover}" style="position: absolute; top:0; left:0; width:100%; height:100%; object-fit: cover;" onerror="this.src='/img/default-cover.png'" />
                        <span style="position: absolute; top: 8px; left: 8px; background: rgba(0,0,0,0.6); color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600;">
                            ${icon} ${typeLabel}
                        </span>
                        ${cornerControl}
                    </div>

                    <div style="padding: 10px; flex: 1; display: flex; flex-direction: column; justify-content: space-between;">
                        <div style="font-size: 0.85rem; font-weight: 600; color: var(--text-primary); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; height: 32px; margin-bottom: 8px; line-height: 1.25; word-break: break-all;">
                            ${title}
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 0.75rem; color: var(--text-muted);">
                            ${footerLeft}
                            <span>❤️ ${note.liked}</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    },

    openInBrowser() {
        const acc = this._accounts.find(a => a.user_id === this._selectedUserId);
        const url = acc ? acc.url : null;
        if (url) {
            window.open(url, '_blank');
            Toast.info('已打开博主主页：点开目标笔记 → 复制地址栏链接 → 到「链接下载」页粘贴下载。');
        } else {
            Toast.warning('未找到该博主主页链接');
        }
    },

    toggleNoteSelection(idx) {
        const checkbox = document.getElementById(`xhs-note-check-${idx}`);
        if (checkbox) {
            checkbox.checked = !checkbox.checked;
            this.updateDownloadButton();
        }
    },

    selectAll() {
        this._notes.forEach((_, idx) => {
            const checkbox = document.getElementById(`xhs-note-check-${idx}`);
            if (checkbox) checkbox.checked = true;
        });
        this.updateDownloadButton();
    },

    deselectAll() {
        this._notes.forEach((_, idx) => {
            const checkbox = document.getElementById(`xhs-note-check-${idx}`);
            if (checkbox) checkbox.checked = false;
        });
        this.updateDownloadButton();
    },

    updateDownloadButton() {
        const downloadBtn = document.getElementById('btn-xhs-download-selected');
        if (!downloadBtn) return;

        const selectedCount = this.getSelectedNotes().length;
        downloadBtn.disabled = selectedCount === 0;
        downloadBtn.textContent = `📥 下载选中 (${selectedCount})`;
    },

    getSelectedNotes() {
        const selected = [];
        this._notes.forEach((note, idx) => {
            const checkbox = document.getElementById(`xhs-note-check-${idx}`);
            if (checkbox && checkbox.checked) {
                selected.push(note);
            }
        });
        return selected;
    },

    async downloadSelected() {
        const selected = this.getSelectedNotes();
        if (selected.length === 0) return;

        try {
            const res = await API.xhs.downloadNotes(selected, this._selectedAccountName);
            if (res.task_id) {
                this.showProgressModal(res.task_id, selected.length);
            } else {
                throw new Error('启动任务失败');
            }
        } catch (err) {
            Toast.error('启动下载失败: ' + err.message);
        }
    },

    showProgressModal(taskId, total) {
        let isDownloading = true;

        Modal.open({
            title: '📥 正在下载小红书笔记',
            content: `
                <div style="padding: 10px 0;">
                    <p style="font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 20px;" id="xhs-progress-modal-desc">
                        正在准备下载，共计 <strong style="color: var(--text-primary); font-size: 1.1rem;">${total}</strong> 篇笔记...
                    </p>
                    <div style="background: var(--bg-tertiary); border-radius: 8px; height: 16px; overflow: hidden; margin-bottom: 12px; position: relative;">
                        <div id="xhs-progress-modal-bar" style="background: var(--primary); height: 100%; width: 0%; transition: width 0.3s; border-radius: 8px;"></div>
                    </div>
                    <div style="display: flex; justify-content: space-between; font-size: 0.9rem;">
                        <span id="xhs-progress-modal-text" style="font-weight: 500; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 70%;">准备中...</span>
                        <span id="xhs-progress-modal-percent" style="font-weight: 600; color: var(--primary);">0%</span>
                    </div>
                </div>
            `,
            footer: `
                <button class="btn btn-secondary" id="btn-xhs-progress-cancel" style="background: #ff3b30; color: white; border-color: rgba(255,59,48,0.2);">取消下载</button>
            `,
            onClose: () => {
                isDownloading = false;
                if (this._pollTimer) {
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                }
            }
        });

        const cancelBtn = document.getElementById('btn-xhs-progress-cancel');
        if (cancelBtn) {
            cancelBtn.onclick = async () => {
                cancelBtn.disabled = true;
                cancelBtn.textContent = '正在取消...';
                try {
                    await API.xhs.cancelDownload(taskId);
                } catch (e) {
                    console.error('Cancel task error:', e);
                }
            };
        }

        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = setInterval(async () => {
            if (!isDownloading) {
                clearInterval(this._pollTimer);
                return;
            }

            try {
                const status = await API.xhs.downloadStatus(taskId);
                const total_count = status.total || total;
                const completed = status.completed || 0;
                const failed = status.failed || 0;
                const skipped = status.skipped || 0;
                const progress_total = completed + failed + skipped;
                
                const pct = total_count > 0 ? Math.round((progress_total / total_count) * 100) : 0;
                
                const bar = document.getElementById('xhs-progress-modal-bar');
                const percent = document.getElementById('xhs-progress-modal-percent');
                const txt = document.getElementById('xhs-progress-modal-text');
                const desc = document.getElementById('xhs-progress-modal-desc');

                if (bar) bar.style.width = `${pct}%`;
                if (percent) percent.textContent = `${pct}%`;
                if (txt) txt.textContent = status.current || '正在下载...';
                if (desc) {
                    desc.innerHTML = `已下载: <strong style="color:var(--success);">${completed}</strong> 篇 | 失败: <strong style="color:var(--error);">${failed}</strong> 篇 | 跳过: <strong style="color:var(--text-muted);">${skipped}</strong> 篇 (共 ${total_count} 篇)`;
                }

                if (status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled') {
                    isDownloading = false;
                    clearInterval(this._pollTimer);
                    this._pollTimer = null;
                    Modal.close();

                    if (status.status === 'completed') {
                        Toast.success(`📥 下载结束！成功: ${completed}，失败: ${failed}，跳过: ${skipped}`);
                    } else if (status.status === 'cancelled') {
                        Toast.info('下载已取消');
                    } else {
                        Toast.error('下载任务失败');
                    }
                    this.deselectAll();
                }
            } catch (err) {
                console.error("Polling download status error:", err);
            }
        }, 1000);
    },

    _esc(s) {
        if (!s) return '';
        const div = document.createElement('div');
        div.textContent = s;
        return div.innerHTML;
    }
};
