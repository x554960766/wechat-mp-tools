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
            if (e.target === this.overlay) {
                if (this.preventClose) return;
                this.close();
            }
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.overlay.classList.contains('active')) {
                if (this.preventClose) return;
                this.close();
            }
        });
    },

    open(options = {}) {
        if (!this.overlay) this.init();

        const { title = '', content = '', footer = '', onClose = null, preventClose = false } = options;
        this.preventClose = preventClose;

        this.dialog.innerHTML = `
            <div class="modal-header">
                <h3 class="modal-title">${title}</h3>
                ${preventClose ? '' : '<button class="modal-close" onclick="Modal.close()">&times;</button>'}
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
        this._onConfirm = onConfirm;
        this.open({
            title,
            content: `<p style="color: var(--text-secondary)">${message}</p>`,
            footer: `
                <button class="btn btn-secondary" onclick="Modal.close()">取消</button>
                <button class="btn btn-primary" onclick="Modal.handleConfirm()">确定</button>
            `,
        });
    },

    handleConfirm() {
        this.close();
        const onConfirm = this._onConfirm;
        this._onConfirm = null;
        if (onConfirm) {
            onConfirm();
        }
    },
};
