/**
 * 抖音评论下载控制组件 (提供全局默认设置与单次弹窗微调)
 */
const DyCommentDialog = {
    modalId: 'dy-comment-dialog-modal',
    onConfirm: null,

    getConfig() {
        let config = {
            defaultCount: 100,
            includeReplies: false
        };
        try {
            const saved = JSON.parse(localStorage.getItem('dy_comment_config'));
            if (saved) config = Object.assign(config, saved);
        } catch (e) {}
        return config;
    },

    saveConfig(config) {
        try {
            localStorage.setItem('dy_comment_config', JSON.stringify(config));
        } catch (e) {}
    },

    open({ title, onConfirm }) {
        const config = this.getConfig();
        let modal = document.getElementById(this.modalId);
        if (!modal) {
            modal = document.createElement('div');
            modal.id = this.modalId;
            document.body.appendChild(modal);
        }

        modal.innerHTML = `
            <div class="modal-content card" style="max-width: 480px; width: 90%; margin: auto; padding: 24px; border-radius: 16px; border: 1px solid var(--border-color); background: var(--bg-secondary); box-shadow: var(--shadow-lg); animation: scaleIn 0.2s ease;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <div style="font-size: 1.15rem; font-weight: 700; color: #ffffff; display: flex; align-items: center; gap: 8px;">
                        <span style="color: var(--primary);">💬</span> 导出视频评论
                    </div>
                    <button onclick="DyCommentDialog.close()" style="background: none; border: none; font-size: 1.2rem; color: var(--text-muted); cursor: pointer; padding: 4px;" onmouseenter="this.style.color='#fff'" onmouseleave="this.style.color='var(--text-muted)'">✕</button>
                </div>

                <div style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 18px; padding: 10px 12px; background: rgba(255,255,255,0.04); border-radius: 8px; border: 1px solid var(--border-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                    <span style="color: var(--text-muted);">视频：</span>${this.escapeHtml(title || '未知视频')}
                </div>

                <div style="margin-bottom: 16px;">
                    <label style="display: block; font-size: 0.88rem; font-weight: 600; color: var(--text-primary); margin-bottom: 8px;">抓取评论条数</label>
                    <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px;" id="dy-comment-pills">
                        <button type="button" class="btn btn-sm dy-count-pill ${config.defaultCount === 50 ? 'active' : ''}" onclick="DyCommentDialog.selectCount(50, this)" style="padding: 4px 12px; font-size: 0.82rem; border-radius: 20px;">50 条</button>
                        <button type="button" class="btn btn-sm dy-count-pill ${config.defaultCount === 100 ? 'active' : ''}" onclick="DyCommentDialog.selectCount(100, this)" style="padding: 4px 12px; font-size: 0.82rem; border-radius: 20px;">100 条 (推荐)</button>
                        <button type="button" class="btn btn-sm dy-count-pill ${config.defaultCount === 300 ? 'active' : ''}" onclick="DyCommentDialog.selectCount(300, this)" style="padding: 4px 12px; font-size: 0.82rem; border-radius: 20px;">300 条</button>
                        <button type="button" class="btn btn-sm dy-count-pill ${config.defaultCount === 500 ? 'active' : ''}" onclick="DyCommentDialog.selectCount(500, this)" style="padding: 4px 12px; font-size: 0.82rem; border-radius: 20px;">500 条</button>
                        <button type="button" class="btn btn-sm dy-count-pill ${config.defaultCount === 0 ? 'active' : ''}" onclick="DyCommentDialog.selectCount(0, this)" style="padding: 4px 12px; font-size: 0.82rem; border-radius: 20px;">全部评论</button>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <input type="number" id="dy-comment-count-input" class="form-input" min="0" value="${config.defaultCount}" placeholder="自定义条数 (填0为全部)" style="width: 100%; height: 38px;">
                        <span style="font-size: 0.82rem; color: var(--text-muted); white-space: nowrap;">条 (0=全部)</span>
                    </div>
                </div>

                <div style="margin-bottom: 16px; padding: 12px; background: rgba(102, 126, 234, 0.08); border-radius: 10px; border: 1px solid rgba(102, 126, 234, 0.2);">
                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.9rem; font-weight: 500; color: #ffffff;">
                        <input type="checkbox" id="dy-comment-replies-input" ${config.includeReplies ? 'checked' : ''} style="width: 16px; height: 16px; accent-color: var(--primary);">
                        <span>抓取楼中楼回复（二级评论）</span>
                    </label>
                    <p style="margin: 4px 0 0 24px; font-size: 0.75rem; color: var(--text-muted); line-height: 1.4;">
                        💡 提示：二级评论耗时较长，若只需主热评建议关闭，速度提升 80% 以上并避免风控。
                    </p>
                </div>

                <div style="margin-bottom: 20px;">
                    <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-size: 0.82rem; color: var(--text-secondary);">
                        <input type="checkbox" id="dy-comment-save-default" style="width: 15px; height: 15px; accent-color: var(--primary);">
                        <span>记住此设置作为全局默认</span>
                    </label>
                </div>

                <div style="display: flex; justify-content: flex-end; gap: 10px;">
                    <button class="btn btn-secondary" onclick="DyCommentDialog.close()" style="padding: 8px 18px;">取消</button>
                    <button class="btn btn-primary" id="dy-comment-confirm-btn" onclick="DyCommentDialog.submit()" style="padding: 8px 24px;">开始导出</button>
                </div>
            </div>
        `;

        modal.style.display = 'flex';
        modal.style.position = 'fixed';
        modal.style.top = '0';
        modal.style.left = '0';
        modal.style.width = '100%';
        modal.style.height = '100%';
        modal.style.background = 'rgba(0, 0, 0, 0.65)';
        modal.style.backdropFilter = 'blur(4px)';
        modal.style.zIndex = '9999';
        modal.style.alignItems = 'center';
        modal.style.justifyContent = 'center';

        this.onConfirm = onConfirm;
        this.updatePillStyles();
    },

    selectCount(count, btn) {
        const input = document.getElementById('dy-comment-count-input');
        if (input) input.value = count;
        document.querySelectorAll('.dy-count-pill').forEach(el => {
            el.classList.remove('active');
            el.style.background = 'rgba(255,255,255,0.06)';
            el.style.borderColor = 'var(--border-color)';
            el.style.color = 'var(--text-primary)';
        });
        if (btn) {
            btn.classList.add('active');
            btn.style.background = 'var(--primary)';
            btn.style.borderColor = 'var(--primary)';
            btn.style.color = '#fff';
        }
    },

    updatePillStyles() {
        document.querySelectorAll('.dy-count-pill').forEach(el => {
            if (el.classList.contains('active')) {
                el.style.background = 'var(--primary)';
                el.style.borderColor = 'var(--primary)';
                el.style.color = '#fff';
            } else {
                el.style.background = 'rgba(255,255,255,0.06)';
                el.style.borderColor = 'var(--border-color)';
                el.style.color = 'var(--text-primary)';
            }
        });
    },

    close() {
        const modal = document.getElementById(this.modalId);
        if (modal) modal.style.display = 'none';
        this.onConfirm = null;
    },

    submit() {
        const countInput = document.getElementById('dy-comment-count-input');
        const repliesInput = document.getElementById('dy-comment-replies-input');
        const saveDefault = document.getElementById('dy-comment-save-default');

        const maxComments = countInput ? Math.max(0, parseInt(countInput.value, 10) || 0) : 100;
        const includeReplies = repliesInput ? repliesInput.checked : false;

        if (saveDefault && saveDefault.checked) {
            this.saveConfig({ defaultCount: maxComments, includeReplies: includeReplies });
            Toast.show('已更新评论导出默认偏好', 'success');
        }

        const cb = this.onConfirm;
        this.close();
        if (cb) {
            cb({ max_comments: maxComments, include_replies: includeReplies });
        }
    },

    escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }
};
