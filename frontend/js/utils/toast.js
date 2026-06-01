/**
 * Toast 通知组件
 */
const Toast = {
    container: null,

    init() {
        this.container = document.getElementById('toast-container');
    },

    show(message, type = 'info', duration = 4000) {
        if (!this.container) this.init();

        const icons = {
            success: '✅',
            error: '❌',
            warning: '⚠️',
            info: 'ℹ️',
        };

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <span class="toast-icon">${icons[type] || icons.info}</span>
            <div class="toast-content">
                <div class="toast-message">${message}</div>
            </div>
            <button class="toast-close" onclick="Toast.dismiss(this.parentElement)">&times;</button>
        `;

        this.container.appendChild(toast);

        if (duration > 0) {
            setTimeout(() => this.dismiss(toast), duration);
        }

        return toast;
    },

    dismiss(toast) {
        if (!toast || !toast.parentElement) return;
        toast.classList.add('toast-exit');
        setTimeout(() => toast.remove(), 300);
    },

    success(msg, dur) { return this.show(msg, 'success', dur); },
    error(msg, dur) { return this.show(msg, 'error', dur); },
    warning(msg, dur) { return this.show(msg, 'warning', dur); },
    info(msg, dur) { return this.show(msg, 'info', dur); },
};
