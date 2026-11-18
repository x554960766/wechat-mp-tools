/* 共享图片查看器（普通大图 + VR 全景）。
 *
 * 从 media_search 抽取，供多个页面复用：media_search 用前缀 ms，全局文件检索用 fv。
 * 前缀只作用于 DOM id（真正的同名冲突点），样式类沿用 .ms-viewer 系列，避免复制一套 CSS。
 *
 * 查看器本身不感知业务：标签读取/写入、NSFW 复核等页面特有行为由页面通过 handlers 注入。
 * 全景口径与媒体服务自带页一致：等距柱状图贴到内向球体，上下立体只取上半幅。
 */
class MediaViewer {
    constructor(prefix = 'ms') {
        this.prefix = prefix;
        this._viewer = null;
        this._keys = null;
        this._three = null;
        this._threePromise = null;
        this._tagSeq = 0;
    }

    /** 按前缀取 DOM id，避免同页多个查看器（理论上不会同时存在）互相踩踏。 */
    _id(name) {
        return `${this.prefix}-${name}`;
    }

    /** 大图地址。列表项已带 file 时直接用，否则按文件行拼 /media/api/media/file。 */
    _fileUrl(image) {
        if (image && image.file) return `/media${image.file}`;
        const version = image && image.content_version != null ? image.content_version : '';
        return `/media/api/media/file?root=${encodeURIComponent(image.root || '')}`
            + `&relpath=${encodeURIComponent(image.relpath || '')}`
            + `&version=${encodeURIComponent(version)}&display=1`;
    }

    open(images, index, handlers = {}) {
        this.close();
        if (!images || !images.length) return;
        const box = document.createElement('div');
        box.className = 'ms-viewer';
        const withReview = typeof handlers.getNsfw === 'function';
        const withTags = typeof handlers.loadTags === 'function';
        box.innerHTML = `
            <div class="ms-viewer-panel" role="dialog" aria-modal="true" aria-label="图片查看">
                <div class="ms-viewer-bar">
                    <span class="form-hint" id="${this._id('viewer-count')}"></span>
                    <span class="ms-viewer-spacer"></span>
                    <button class="btn btn-secondary btn-sm" data-act="prev">上一张</button>
                    <button class="btn btn-secondary btn-sm" data-act="next">下一张</button>
                    <span class="ms-viewer-pano-controls" id="${this._id('pano-controls')}" hidden>
                        <label class="form-hint">全景模式
                            <select class="form-input" id="${this._id('pano-mode')}">
                                <option value="mono">单目</option>
                                <option value="stereo">上下立体（取上半幅）</option>
                            </select>
                        </label>
                        <button class="btn btn-secondary btn-sm" data-act="pano-exit">退出全景</button>
                    </span>
                    ${withReview ? `<span class="ms-viewer-review" id="${this._id('viewer-review')}" hidden>
                        <button class="btn btn-secondary btn-sm" data-act="review-nsfw">确认敏感</button>
                        <button class="btn btn-secondary btn-sm" data-act="review-safe">标为误报</button>
                    </span>` : ''}
                    <button class="btn btn-primary btn-sm" data-act="pano">全景查看</button>
                    <button class="btn btn-secondary btn-sm" data-act="close">关闭</button>
                </div>
                <div class="ms-viewer-stage">
                    <img id="${this._id('viewer-img')}" alt="">
                    <div class="ms-viewer-pano" id="${this._id('viewer-pano')}" hidden></div>
                </div>
                ${withTags ? `<div class="ms-viewer-tags" id="${this._id('viewer-tags')}">
                    <span class="ms-tag-chips" id="${this._id('tag-chips')}"></span>
                    <input class="form-input ms-tag-input" id="${this._id('tag-input')}" type="text"
                           maxlength="32" placeholder="加标签后回车" autocomplete="off">
                </div>` : ''}
                <p class="form-hint" id="${this._id('viewer-status')}" role="status" hidden></p>
            </div>`;
        document.body.appendChild(box);
        this._handlers = handlers;
        this._viewer = { box, images, index: 0, seq: 0, pano: null };

        box.addEventListener('click', (event) => {
            if (event.target === box) {
                this.close();
                return;
            }
            const act = event.target.closest('[data-act]');
            if (!act) return;
            const name = act.dataset.act;
            if (name === 'close') this.close();
            else if (name === 'prev') this.step(-1);
            else if (name === 'next') this.step(1);
            else if (name === 'pano') this.panorama();
            else if (name === 'pano-exit') this.exitPano();
            else if (name === 'review-nsfw') this.review('nsfw');
            else if (name === 'review-safe') this.review('safe');
            else if (name === 'tag-remove') this.editTag(act.dataset.tag, 'remove');
        });

        const mode = document.getElementById(this._id('pano-mode'));
        if (mode) mode.addEventListener('change', () => this.applyPanoMode());
        const tagInput = document.getElementById(this._id('tag-input'));
        if (tagInput) tagInput.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault(); // 回车只提交标签，别冒泡出去触发别的提交
            this.editTag(tagInput.value, 'add');
        });

        this._keys = (event) => {
            if (!this._viewer) return;
            if (event.key === 'Escape') {
                this.close();
                return;
            }
            if (this._viewer.pano) return; // 全景下方向键留给场景拖动
            if (event.key === 'ArrowLeft') this.step(-1);
            if (event.key === 'ArrowRight') this.step(1);
        };
        document.addEventListener('keydown', this._keys);
        this.show(index);
    }

    show(index) {
        const viewer = this._viewer;
        if (!viewer) return;
        const total = viewer.images.length;
        viewer.index = ((Math.trunc(index) % total) + total) % total;
        const image = viewer.images[viewer.index];
        this.exitPano();
        const img = document.getElementById(this._id('viewer-img'));
        const count = document.getElementById(this._id('viewer-count'));
        if (count) count.textContent = `${viewer.index + 1} / ${total} · ${image.relpath || ''}`;
        if (img) {
            img.src = this._fileUrl(image);
            img.alt = image.relpath || '';
            img.hidden = false;
        }
        const handlers = this._handlers || {};
        const nsfw = typeof handlers.getNsfw === 'function' ? handlers.getNsfw(image) : null;
        const risky = !!(nsfw && nsfw.label === 'nsfw');
        const reviewed = !!(nsfw && nsfw.stage === 'second');
        const score = nsfw && nsfw.score != null ? Number(nsfw.score).toFixed(3) : '—';
        this.status(risky
            ? (reviewed ? `人工复核：确认敏感（分数 ${score}）` : `初筛 疑似 NSFW（${score}）`)
            : (reviewed ? '人工复核：已标为误报' : ''));
        // 有机器初筛结论才提供复核入口：没有证据的图不该被随手标记
        const reviewBox = document.getElementById(this._id('viewer-review'));
        if (reviewBox) reviewBox.hidden = !nsfw;
        this.loadTags(image);
    }

    /** 人工复核当前大图：写 second 级结论，机器重扫只动 first 行，不会覆盖它。 */
    async review(label) {
        const viewer = this._viewer;
        const handlers = this._handlers || {};
        if (!viewer || typeof handlers.review !== 'function') return;
        const image = viewer.images[viewer.index];
        if (!image || !image.root || !image.relpath) return;
        const box = document.getElementById(this._id('viewer-review'));
        const buttons = box ? box.querySelectorAll('button') : [];
        buttons.forEach((button) => { button.disabled = true; });
        try {
            await handlers.review(image, label);
            this.show(viewer.index); // 状态行与按钮跟着新结论刷新
        } catch (err) {
            const Toast = window.Toast;
            if (Toast && Toast.error) Toast.error('复核失败：' + (err && err.message ? err.message : '请求失败'));
        } finally {
            buttons.forEach((button) => { button.disabled = false; });
        }
    }

    /** 当前大图的标签。数据由页面注入的 loadTags 提供，查看器只管渲染与代次防串。 */
    async loadTags(image) {
        const chips = document.getElementById(this._id('tag-chips'));
        const handlers = this._handlers || {};
        if (!chips || typeof handlers.loadTags !== 'function') return;
        const seq = ++this._tagSeq;
        this.renderTags([]);
        if (!image || !image.root || !image.relpath) return;
        try {
            const tags = await handlers.loadTags(image);
            if (seq !== this._tagSeq) return; // 切图之后回来的旧响应不许覆盖新图
            this.renderTags(tags);
        } catch (err) {
            if (seq === this._tagSeq) this.renderTags([]);
        }
    }

    /** 用 DOM 构建而不是拼 HTML：标签是用户输入，拼字符串就得额外做转义。 */
    renderTags(tags) {
        const chips = document.getElementById(this._id('tag-chips'));
        if (!chips) return;
        chips.textContent = '';
        if (!tags || !tags.length) {
            const hint = document.createElement('span');
            hint.className = 'form-hint';
            hint.textContent = '未打标签';
            chips.appendChild(hint);
            return;
        }
        tags.forEach((tag) => {
            const chip = document.createElement('span');
            chip.className = 'ms-tag-chip';
            chip.textContent = tag;
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.dataset.act = 'tag-remove';
            remove.dataset.tag = tag;
            remove.title = '移除标签';
            remove.textContent = '×';
            chip.appendChild(remove);
            chips.appendChild(chip);
        });
    }

    /** 打标签 / 去标签；成功后用后端返回的完整列表刷新，不在前端猜增删结果。 */
    async editTag(tag, mode) {
        const viewer = this._viewer;
        const handlers = this._handlers || {};
        const text = (tag || '').trim();
        if (!viewer || !text || typeof handlers.saveTag !== 'function') return;
        const image = viewer.images[viewer.index];
        if (!image || !image.root || !image.relpath) return;
        const input = document.getElementById(this._id('tag-input'));
        if (input) input.disabled = true;
        try {
            const tags = await handlers.saveTag(image, text, mode);
            if (input && mode === 'add') input.value = '';
            this.renderTags(tags);
            if (typeof handlers.onTagsChanged === 'function') handlers.onTagsChanged();
        } catch (err) {
            const Toast = window.Toast;
            if (Toast && Toast.error) Toast.error('标签保存失败：' + (err && err.message ? err.message : '请求失败'));
        } finally {
            if (input) { input.disabled = false; input.focus(); }
        }
    }

    step(delta) {
        const viewer = this._viewer;
        if (!viewer) return;
        this.show(viewer.index + delta);
    }

    async panorama() {
        const viewer = this._viewer;
        if (!viewer) return;
        const image = viewer.images[viewer.index];
        this.exitPano();
        // 代次必须在第一个 await 之前建立：等待 Three.js 期间退出或切图要能作废本次加载，
        // 否则回来时会往已经关掉的查看器里塞场景。
        const token = ++viewer.seq;
        this.status('正在加载全景…');
        let THREE = null;
        try {
            THREE = await this.loadThree();
        } catch (err) {
            if (this._viewer === viewer && token === viewer.seq) {
                this.status('Three.js 加载失败：' + (err && err.message ? err.message : '网络不可用'));
            }
            return;
        }
        if (this._viewer !== viewer || token !== viewer.seq) return;
        this.buildPano(THREE, image, viewer);
    }

    async loadThree() {
        if (this._three) return this._three;
        if (!this._threePromise) {
            // 固定版本，与媒体服务自带页保持一致；只在首次点「全景查看」时才下载
            this._threePromise = import('https://unpkg.com/three@0.160.0/build/three.module.min.js')
                .then((mod) => {
                    this._three = mod;
                    return mod;
                })
                .catch((err) => {
                    this._threePromise = null;
                    throw err;
                });
        }
        return this._threePromise;
    }

    buildPano(THREE, image, viewer) {
        const stage = document.getElementById(this._id('viewer-pano'));
        const img = document.getElementById(this._id('viewer-img'));
        if (!stage || !viewer) return;
        const width = stage.clientWidth || 800;
        const height = stage.clientHeight || 450;
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(width, height, false);
        stage.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(90, width / height, 0.1, 1100);
        const geometry = new THREE.SphereGeometry(500, 64, 40);
        geometry.scale(-1, 1, 1); // 法线朝内：等于把相机放在球心往外看
        const texture = new THREE.TextureLoader().load(this._fileUrl(image), () => {
            if (viewer.pano && viewer.pano.texture === texture) this.status('拖动旋转，滚轮缩放');
        }, undefined, () => {
            if (viewer.pano && viewer.pano.texture === texture) this.status('全景原图加载失败');
        });
        if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
        const material = new THREE.MeshBasicMaterial({ map: texture });
        const mesh = new THREE.Mesh(geometry, material);
        scene.add(mesh);
        const pano = {
            renderer, scene, camera, geometry, material, texture, mesh,
            yaw: 0, pitch: 0, fov: 90, mode: 'mono', raf: 0,
        };
        viewer.pano = pano;
        stage.hidden = false;
        if (img) img.hidden = true;
        const controls = document.getElementById(this._id('pano-controls'));
        if (controls) controls.hidden = false;
        this.applyPanoMode();
        const canvas = renderer.domElement;
        let dragging = false;
        let lastX = 0;
        let lastY = 0;
        const onDown = (event) => {
            dragging = true;
            lastX = event.clientX;
            lastY = event.clientY;
            if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
        };
        const onMove = (event) => {
            if (!dragging) return;
            pano.yaw += (event.clientX - lastX) * 0.0045;
            const limit = Math.PI / 2 - 0.05;
            pano.pitch = Math.max(-limit, Math.min(limit, pano.pitch + (event.clientY - lastY) * 0.0045));
            lastX = event.clientX;
            lastY = event.clientY;
        };
        const onUp = () => { dragging = false; };
        const onWheel = (event) => {
            event.preventDefault();
            pano.fov = Math.max(35, Math.min(100, pano.fov + Math.sign(event.deltaY) * 4));
            camera.fov = pano.fov;
            camera.updateProjectionMatrix();
        };
        canvas.addEventListener('pointerdown', onDown);
        canvas.addEventListener('pointermove', onMove);
        canvas.addEventListener('pointerup', onUp);
        canvas.addEventListener('pointerleave', onUp);
        canvas.addEventListener('wheel', onWheel, { passive: false });
        pano.detach = () => {
            canvas.removeEventListener('pointerdown', onDown);
            canvas.removeEventListener('pointermove', onMove);
            canvas.removeEventListener('pointerup', onUp);
            canvas.removeEventListener('pointerleave', onUp);
            canvas.removeEventListener('wheel', onWheel);
        };
        const resize = () => {
            if (!viewer.pano) return;
            const w = stage.clientWidth || width;
            const h = stage.clientHeight || height;
            renderer.setSize(w, h, false);
            camera.aspect = w / h;
            camera.updateProjectionMatrix();
        };
        pano.resize = resize;
        window.addEventListener('resize', resize);
        const loop = () => {
            if (!viewer.pano || viewer.pano !== pano) return;
            camera.rotation.order = 'YXZ';
            camera.rotation.y = pano.yaw;
            camera.rotation.x = pano.pitch;
            renderer.render(scene, camera);
            pano.raf = requestAnimationFrame(loop);
        };
        loop();
    }

    applyPanoMode() {
        const viewer = this._viewer;
        const pano = viewer && viewer.pano;
        if (!pano) return;
        const select = document.getElementById(this._id('pano-mode'));
        pano.mode = select ? select.value : 'mono';
        if (pano.mode === 'stereo') {
            // 上下立体：v 取 [0.5,1]，即整幅图的上半副眼图
            pano.texture.repeat.y = 0.5;
            pano.texture.offset.y = 0.5;
        } else {
            pano.texture.repeat.y = 1;
            pano.texture.offset.y = 0;
        }
        pano.texture.needsUpdate = true;
    }

    exitPano() {
        const viewer = this._viewer;
        if (!viewer) return;
        const pano = viewer.pano;
        viewer.pano = null;
        viewer.seq += 1; // 作废仍在等待的加载
        this.disposePano(pano);
        const stage = document.getElementById(this._id('viewer-pano'));
        if (stage) {
            stage.hidden = true;
            stage.innerHTML = '';
        }
        const img = document.getElementById(this._id('viewer-img'));
        if (img) img.hidden = false;
        const controls = document.getElementById(this._id('pano-controls'));
        if (controls) controls.hidden = true;
        if (pano) this.status('');
    }

    disposePano(pano) {
        if (!pano) return;
        if (pano.raf) cancelAnimationFrame(pano.raf);
        if (pano.resize) window.removeEventListener('resize', pano.resize);
        if (pano.detach) pano.detach();
        try { pano.scene.remove(pano.mesh); } catch (err) { /* 忽略：资源已释放 */ }
        try { pano.geometry.dispose(); } catch (err) { /* 同上 */ }
        try { pano.texture.dispose(); } catch (err) { /* 同上 */ }
        try { pano.material.dispose(); } catch (err) { /* 同上 */ }
        const canvas = pano.renderer && pano.renderer.domElement;
        try { pano.renderer.dispose(); } catch (err) { /* 同上 */ }
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
    }

    status(msg) {
        const el = document.getElementById(this._id('viewer-status'));
        if (!el) return;
        el.textContent = msg || '';
        el.hidden = !msg;
    }

    close() {
        const viewer = this._viewer;
        if (!viewer) return;
        this._viewer = null;
        viewer.seq += 1;
        if (this._keys) {
            document.removeEventListener('keydown', this._keys);
            this._keys = null;
        }
        const pano = viewer.pano;
        viewer.pano = null;
        this.disposePano(pano);
        if (viewer.box && viewer.box.parentNode) viewer.box.parentNode.removeChild(viewer.box);
    }
}

window.MediaViewer = MediaViewer;
