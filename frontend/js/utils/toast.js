/**
 * Toast 通知组件 (升级版：支持标题、富文本、SVG图标、倒计时进度条与悬停暂停)
 */
const Toast = {
    container: null,

    init() {
        this.container = document.getElementById('toast-container');
        if (!this.container) {
            this.container = document.createElement('div');
            this.container.id = 'toast-container';
            this.container.className = 'toast-container';
            document.body.appendChild(this.container);
        }
    },

    show(message, type = 'info', duration = 4500, title = '') {
        if (!this.container) this.init();

        const icons = {
            success: `<svg viewBox="0 0 24 24" fill="none" stroke="#07c160" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`,
            error: `<svg viewBox="0 0 24 24" fill="none" stroke="#ff4d4f" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>`,
            warning: `<svg viewBox="0 0 24 24" fill="none" stroke="#faad14" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`,
            info: `<svg viewBox="0 0 24 24" fill="none" stroke="#1890ff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`,
        };

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        
        const titleHtml = title ? `<div class="toast-title">${title}</div>` : '';
        const progressHtml = duration > 0 ? `<div class="toast-progress toast-progress-${type}" style="animation-duration: ${duration}ms;"></div>` : '';

        toast.innerHTML = `
            <div class="toast-icon">${icons[type] || icons.info}</div>
            <div class="toast-content">
                ${titleHtml}
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close" title="关闭">&times;</button>
            ${progressHtml}
        `;

        const closeBtn = toast.querySelector('.toast-close');
        if (closeBtn) {
            closeBtn.onclick = () => this.dismiss(toast);
        }

        this.container.appendChild(toast);

        if (duration > 0) {
            let timer = null;
            let startTime = Date.now();
            let remaining = duration;

            const startTimer = () => {
                startTime = Date.now();
                timer = setTimeout(() => this.dismiss(toast), remaining);
            };

            const pauseTimer = () => {
                if (timer) {
                    clearTimeout(timer);
                    timer = null;
                    remaining -= (Date.now() - startTime);
                    if (remaining < 500) remaining = 500;
                }
            };

            toast.addEventListener('mouseenter', pauseTimer);
            toast.addEventListener('mouseleave', startTimer);

            startTimer();
        }

        return toast;
    },

    dismiss(toast) {
        if (!toast || !toast.parentElement) return;
        toast.classList.add('toast-exit');
        setTimeout(() => toast.remove(), 250);
    },

    success(msg, dur, title) { return this.show(msg, 'success', dur, title); },
    error(msg, dur, title)   { return this.show(msg, 'error', dur, title); },
    warning(msg, dur, title) { return this.show(msg, 'warning', dur, title); },
    info(msg, dur, title)    { return this.show(msg, 'info', dur, title); },
};
