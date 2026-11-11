/**
 * 腾讯防水墙 (TCaptcha) 前端集成模块
 * ----------------------------------------------------
 * 适配微信读书风控机制 (AppID: 2044038556)
 * 1. 监测到风控拦截 (need_verify / HTTP 429) 时自动拉起滑块验证；
 * 2. 支持顶部通知横幅提示（直接网页验证 / 已在App处理）；
 * 3. 验证通过后自动调用 /api/waterwall/verify 同步 wr_ticket 与 wr_randstr；
 * 4. 自动重试被中断的业务请求。
 */

const WaterWall = {
    APP_ID: '2044038556',
    BANNER_ID: 'waterwall-banner',
    isShowingCaptcha: false,
    lastPendingAction: null,

    /**
     * 初始化检查后端当前是否命中防水墙拦截
     */
    async init() {
        try {
            const resp = await fetch('/api/waterwall');
            if (resp.ok) {
                const data = await resp.json();
                const isActive = data && (data.active || data.need_verify || (data.waterwall && data.waterwall.active));
                if (isActive) {
                    this.renderBanner(data.waterwall || data);
                }
            }
        } catch (e) {
            console.debug('[WaterWall] Status check skipped:', e);
        }
    },

    /**
     * 拉起腾讯官方滑块验证码
     */
    triggerCaptcha(options = {}) {
        const { onSuccess, onCancel } = options;
        if (this.isShowingCaptcha) return;

        if (typeof TencentCaptcha === 'undefined') {
            if (typeof Toast !== 'undefined') {
                Toast.warning('腾讯防水墙组件正在加载，请稍候...');
            } else {
                alert('腾讯防水墙组件正在加载，请稍候...');
            }
            return;
        }

        this.isShowingCaptcha = true;
        try {
            const captcha = new TencentCaptcha(this.APP_ID, async (res) => {
                this.isShowingCaptcha = false;
                if (res && res.ret === 0) {
                    if (typeof Toast !== 'undefined') {
                        Toast.info('验证通过，正在同步安全凭证...');
                    }
                    try {
                        const resp = await fetch('/api/waterwall/verify', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                ticket: res.ticket,
                                randstr: res.randstr,
                                bizState: res.bizState || ''
                            })
                        });
                        const d = await resp.json();
                        if (d && d.ok) {
                            if (typeof Toast !== 'undefined') {
                                Toast.success('安全验证成功！已自动解除拦截，正在恢复请求...');
                            }
                            const el = document.getElementById(this.BANNER_ID);
                            if (el) el.remove();

                            if (onSuccess) {
                                onSuccess(res);
                            } else if (typeof this.lastPendingAction === 'function') {
                                const retryFn = this.lastPendingAction;
                                this.lastPendingAction = null;
                                retryFn();
                            }
                        } else {
                            if (typeof Toast !== 'undefined') {
                                Toast.error('凭据同步异常：' + ((d && d.error) || '未知错误'));
                            }
                        }
                    } catch (e) {
                        if (typeof Toast !== 'undefined') {
                            Toast.error('提交安全验证失败：' + e.message);
                        }
                    }
                } else if (res && res.ret === 2) {
                    if (typeof Toast !== 'undefined') {
                        Toast.info('已取消安全验证');
                    }
                    if (onCancel) onCancel();
                } else {
                    if (typeof Toast !== 'undefined') {
                        Toast.warning('验证未通过，请重新验证');
                    }
                }
            }, {
                needFeedBack: false,
                enableDarkMode: true
            });
            captcha.show();
        } catch (err) {
            this.isShowingCaptcha = false;
            console.error('[WaterWall] Trigger captcha error:', err);
            if (typeof Toast !== 'undefined') {
                Toast.error('拉起防水墙验证码失败：' + err.message);
            }
        }
    },

    /**
     * 渲染顶部安全拦截横幅
     */
    renderBanner(info = {}) {
        let el = document.getElementById(this.BANNER_ID);
        if (!el) {
            el = document.createElement('div');
            el.id = this.BANNER_ID;
            el.innerHTML = `
                <div class="ww-icon">🛡️</div>
                <div class="ww-body">
                    <div class="ww-title">微信读书触发了腾讯安全验证（防水墙）</div>
                    <div class="ww-desc">上游接口触发人机拦截，系统已暂停自动重试以保护账号。请在网页中完成滑块验证或在手机微信读书中阅读任意内容解除。</div>
                </div>
                <div class="ww-actions">
                    <button type="button" class="ww-btn ww-verify-now">立即网页验证</button>
                    <button type="button" class="ww-btn ww-retry">已在App处理</button>
                    <button type="button" class="ww-btn ww-ghost ww-close">✕</button>
                </div>
            `;
            document.body.appendChild(el);

            el.querySelector('.ww-close').addEventListener('click', () => el.remove());
            el.querySelector('.ww-verify-now').addEventListener('click', () => {
                this.triggerCaptcha();
            });
            el.querySelector('.ww-retry').addEventListener('click', async () => {
                const btn = el.querySelector('.ww-retry');
                btn.disabled = true;
                btn.textContent = '解除中…';
                try {
                    await fetch('/api/waterwall/clear', { method: 'POST' });
                    el.remove();
                    if (typeof Toast !== 'undefined') {
                        Toast.success('安全拦截已手动解除，正在恢复...');
                    }
                    if (typeof this.lastPendingAction === 'function') {
                        const retryFn = this.lastPendingAction;
                        this.lastPendingAction = null;
                        retryFn();
                    }
                } catch (e) {
                    btn.disabled = false;
                    btn.textContent = '已在App处理';
                    if (typeof Toast !== 'undefined') {
                        Toast.error('解除失败：' + e.message);
                    }
                }
            });
        }

        const descEl = el.querySelector('.ww-desc');
        if (descEl && info) {
            const hits = info.hits ? `累计拦截 ${info.hits} 次` : '';
            const status = info.last_status ? `HTTP ${info.last_status}` : '';
            const parts = [status, hits].filter(Boolean).join(' | ');
            if (parts) {
                descEl.textContent = `微信读书上游触发人机安全拦截（${parts}）。请点击右侧完成滑块验证或在手机 App 开启阅读后点击“已在App处理”。`;
            }
        }
    },

    /**
     * 当检测到防水墙拦截时调用
     */
    handleDetection(info = {}, retryAction = null) {
        if (retryAction) {
            this.lastPendingAction = retryAction;
        }
        this.renderBanner(info);
        // 自动拉起滑块
        this.triggerCaptcha();
    }
};

// 页面加载完成后自动探针
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => WaterWall.init());
    } else {
        WaterWall.init();
    }
}
