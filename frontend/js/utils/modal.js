/**
 * 模态框组件
 */
const Modal = {
    overlay: null,
    dialog: null,

    init() {
        this.overlay = document.getElementById('modal-overlay');
        this.dialog = document.getElementById('modal-dialog');

        this.overlay.addEventListener('click', (e) => {
            if (e.target === this.overlay) this.close();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.overlay.classList.contains('active')) {
                this.close();
            }
        });
    },

    open(options = {}) {
        if (!this.overlay) this.init();

        const { title = '', content = '', footer = '', onClose = null } = options;

        this.dialog.innerHTML = `
            <div class="modal-header">
                <h3 class="modal-title">${title}</h3>
                <button class="modal-close" onclick="Modal.close()">&times;</button>
            </div>
            <div class="modal-body">${content}</div>
            ${footer ? `<div class="modal-footer">${footer}</div>` : ''}
        `;

        this._onClose = onClose;
        this.overlay.classList.add('active');
    },

    close() {
        if (!this.overlay) return;
        this.overlay.classList.remove('active');
        if (this._onClose) this._onClose();
    },

    confirm(title, message, onConfirm) {
        this.open({
            title,
            content: `<p style="color: var(--text-secondary)">${message}</p>`,
            footer: `
                <button class="btn btn-secondary" onclick="Modal.close()">取消</button>
                <button class="btn btn-primary" onclick="Modal.close(); (${onConfirm.toString()})()">确定</button>
            `,
        });
    },
};
