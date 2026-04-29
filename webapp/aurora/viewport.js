/**
 * Aurora Viewport — modular Three.js 3D viewer.
 *
 * Usage:
 *   import { AuroraViewport } from './aurora/viewport.js';
 *   const vp = new AuroraViewport(document.getElementById('viewer'));
 *   vp.loadModel(url);           // load GLB/GLTF from URL
 *   vp.loadModelFromFile(file);   // load from a File/Blob
 *   vp.dispose();                 // tear down
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RGBELoader } from 'three/addons/loaders/RGBELoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { ViewportGizmo } from 'https://cdn.jsdelivr.net/gh/taucad/three-viewport-gizmo/dist/three-viewport-gizmo.js';

const HDRI_URL = 'https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/1k/flamingo_pan_1k.hdr';
const BG_COLOR = 0x1a1a2e;

export class AuroraViewport {
    /**
     * @param {HTMLElement} container — the DOM element to mount the viewport in.
     * @param {object}      [opts]
     * @param {boolean}     [opts.hdri=true]       show HDRI environment on start
     * @param {boolean}     [opts.grid=true]       show grid on start
     * @param {boolean}     [opts.wireframe=false]  show wireframe on start
     * @param {boolean}     [opts.toolbar=true]    show sidebar toolbar buttons
     */
    constructor(container, opts = {}) {
        this._container = container;
        this._opts = { hdri: true, grid: true, wireframe: false, toolbar: true, ...opts };

        // State
        this._model = null;
        this._wireframes = [];
        this._wireframeEnabled = this._opts.wireframe;
        this._hdriEnabled = this._opts.hdri;
        this._gridEnabled = this._opts.grid;
        this._hdriTexture = null;
        this._originalMaterials = new Map();
        this._animationId = null;
        this._disposed = false;

        this._build();
    }

    /* ------------------------------------------------------------------ */
    /*  Construction                                                       */
    /* ------------------------------------------------------------------ */

    _build() {
        // Wrapper fills the container
        this._wrapper = document.createElement('div');
        this._wrapper.className = 'aurora-viewport';
        this._container.appendChild(this._wrapper);

        // Canvas target
        this._canvasWrap = document.createElement('div');
        this._canvasWrap.className = 'aurora-viewport-canvas';
        this._wrapper.appendChild(this._canvasWrap);

        // Three.js
        this._initThree();

        // Toolbar
        if (this._opts.toolbar) this._buildToolbar();

        // Resize observer — observe the external container (the grid cell)
        this._resizeObserver = new ResizeObserver(() => this._onResize());
        this._resizeObserver.observe(this._container);
    }

    _initThree() {
        this._scene = new THREE.Scene();
        this._scene.background = new THREE.Color(BG_COLOR);

        const w = this._canvasWrap.clientWidth || 1;
        const h = this._canvasWrap.clientHeight || 1;

        this._camera = new THREE.PerspectiveCamera(75, w / h, 0.01, 2000);
        this._camera.position.set(5, 5, 5);

        this._renderer = new THREE.WebGLRenderer({ antialias: true });
        this._renderer.setPixelRatio(window.devicePixelRatio);
        this._renderer.setSize(w, h);
        this._renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this._renderer.toneMappingExposure = 0.6;
        this._renderer.outputColorSpace = THREE.SRGBColorSpace;
        this._canvasWrap.appendChild(this._renderer.domElement);
        this._renderer.domElement.style.position = 'absolute';
        this._renderer.domElement.style.inset = '0';

        // Orbit controls
        this._orbit = new OrbitControls(this._camera, this._renderer.domElement);
        this._orbit.enableDamping = true;
        this._orbit.dampingFactor = 0.05;

        // Gizmo (mount inside the canvas wrapper so it stays within the viewport)
        this._gizmo = new ViewportGizmo(this._camera, this._renderer, {
            container: this._canvasWrap
        });
        this._gizmo.attachControls(this._orbit);

        // Grid
        this._grid = new THREE.GridHelper(20, 20, 0x444444, 0x222222);
        this._grid.visible = this._gridEnabled;
        this._scene.add(this._grid);

        // Fallback lights (used when HDRI is off)
        this._ambientLight = new THREE.AmbientLight(0xffffff, 1.5);
        this._ambientLight.visible = !this._hdriEnabled;
        this._scene.add(this._ambientLight);

        this._dirLight = new THREE.DirectionalLight(0xffffff, 2);
        this._dirLight.position.set(5, 10, 7.5);
        this._dirLight.visible = !this._hdriEnabled;
        this._scene.add(this._dirLight);

        // HDRI
        if (this._hdriEnabled) this._loadHDRI();

        // Render loop
        this._animate();
    }

    _loadHDRI() {
        new RGBELoader().load(HDRI_URL, (texture) => {
            if (this._disposed) return;
            texture.mapping = THREE.EquirectangularReflectionMapping;
            this._hdriTexture = texture;
            this._scene.environment = texture;
            this._scene.background = texture;
            this._scene.backgroundBlurriness = 0.5;
            this._scene.environmentIntensity = 0.8;
        });
    }

    _animate() {
        if (this._disposed) return;
        this._animationId = requestAnimationFrame(() => this._animate());
        this._orbit.update();
        this._renderer.render(this._scene, this._camera);
        this._gizmo.render();
    }

    _onResize() {
        const w = this._wrapper.clientWidth;
        const h = this._wrapper.clientHeight;
        if (w === 0 || h === 0) return;
        this._camera.aspect = w / h;
        this._camera.updateProjectionMatrix();
        this._renderer.setSize(w, h);
        this._gizmo.update();
    }

    /* ------------------------------------------------------------------ */
    /*  Toolbar                                                            */
    /* ------------------------------------------------------------------ */

    _buildToolbar() {
        const bar = document.createElement('div');
        bar.className = 'aurora-viewport-toolbar';

        bar.appendChild(this._makeBtn('hdri', this._hdriEnabled, 'Toggle HDRI Environment',
            `<circle cx="12" cy="12" r="5"/>
             <line x1="12" y1="1" x2="12" y2="3"/>
             <line x1="12" y1="21" x2="12" y2="23"/>
             <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
             <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
             <line x1="1" y1="12" x2="3" y2="12"/>
             <line x1="21" y1="12" x2="23" y2="12"/>
             <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
             <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>`));

        bar.appendChild(this._makeBtn('wireframe', this._wireframeEnabled, 'Toggle Wireframe',
            `<path d="M12 2L2 7l10 5 10-5-10-5z"/>
             <path d="M2 17l10 5 10-5"/>
             <path d="M2 12l10 5 10-5"/>`));

        bar.appendChild(this._makeBtn('grid', this._gridEnabled, 'Toggle Grid',
            `<line x1="3" y1="12" x2="21" y2="12"/>
             <line x1="12" y1="3" x2="12" y2="21"/>
             <line x1="3" y1="6" x2="21" y2="6"/>
             <line x1="3" y1="18" x2="21" y2="18"/>
             <line x1="6" y1="3" x2="6" y2="21"/>
             <line x1="18" y1="3" x2="18" y2="21"/>`));

        this._wrapper.appendChild(bar);
    }

    _makeBtn(id, active, title, svgContent) {
        const btn = document.createElement('button');
        btn.className = 'aurora-vp-btn' + (active ? ' active' : '');
        btn.title = title;
        btn.innerHTML = `<svg viewBox="0 0 24 24">${svgContent}</svg>`;
        btn.addEventListener('click', () => this._onToolbarClick(id, btn));
        return btn;
    }

    _onToolbarClick(id, btn) {
        switch (id) {
            case 'hdri':
                this._hdriEnabled = !this._hdriEnabled;
                btn.classList.toggle('active', this._hdriEnabled);
                this._applyHDRI();
                break;
            case 'wireframe':
                this._wireframeEnabled = !this._wireframeEnabled;
                btn.classList.toggle('active', this._wireframeEnabled);
                if (this._model) {
                    this._wireframeEnabled ? this._addWireframe(this._model) : this._removeWireframe();
                }
                break;
            case 'grid':
                this._gridEnabled = !this._gridEnabled;
                btn.classList.toggle('active', this._gridEnabled);
                this._grid.visible = this._gridEnabled;
                break;
        }
    }

    /* ------------------------------------------------------------------ */
    /*  HDRI helpers                                                        */
    /* ------------------------------------------------------------------ */

    _applyHDRI() {
        if (!this._hdriTexture) return;
        if (this._hdriEnabled) {
            this._scene.environment = this._hdriTexture;
            this._scene.background = this._hdriTexture;
            this._ambientLight.visible = false;
            this._dirLight.visible = false;
            if (this._model) this._restorePBR(this._model);
        } else {
            this._scene.environment = null;
            this._scene.background = new THREE.Color(0x1a1a1a);
            this._ambientLight.visible = true;
            this._dirLight.visible = true;
            if (this._model) this._switchToLambert(this._model);
        }
    }

    _switchToLambert(obj) {
        obj.traverse((child) => {
            if (!child.isMesh) return;
            if (!this._originalMaterials.has(child)) this._originalMaterials.set(child, child.material);
            child.material = new THREE.MeshLambertMaterial({ color: 0x808080 });
        });
    }

    _restorePBR(obj) {
        obj.traverse((child) => {
            if (!child.isMesh || !this._originalMaterials.has(child)) return;
            const old = child.material;
            child.material = this._originalMaterials.get(child);
            if (old?.dispose) old.dispose();
        });
    }

    /* ------------------------------------------------------------------ */
    /*  Wireframe helpers                                                   */
    /* ------------------------------------------------------------------ */

    _addWireframe(obj) {
        obj.traverse((child) => {
            if (!child.isMesh) return;
            const wire = new THREE.LineSegments(
                new THREE.WireframeGeometry(child.geometry),
                new THREE.LineBasicMaterial({ color: 0xff6600, transparent: true, opacity: 1.0 })
            );
            wire.position.copy(child.position);
            wire.rotation.copy(child.rotation);
            wire.scale.copy(child.scale);
            child.parent.add(wire);
            this._wireframes.push(wire);
        });
    }

    _removeWireframe() {
        this._wireframes.forEach((w) => {
            w.parent?.remove(w);
            w.geometry.dispose();
            w.material.dispose();
        });
        this._wireframes = [];
    }

    /* ------------------------------------------------------------------ */
    /*  Public API                                                         */
    /* ------------------------------------------------------------------ */

    /**
     * Load a GLB/GLTF model from a URL.
     * @param {string}  url
     * @param {object}  [opts]
     * @param {boolean} [opts.resetView=false] — reset scale & camera (use for new HDA)
     * @returns {Promise<void>}
     */
    loadModel(url, opts = {}) {
        if (opts.resetView) this._modelScale = null;
        return new Promise((resolve, reject) => {
            new GLTFLoader().load(
                url,
                (gltf) => {
                    this._setModel(gltf.scene);
                    resolve();
                },
                undefined,
                (err) => {
                    console.error('[AuroraViewport] Failed to load model:', err);
                    reject(err);
                }
            );
        });
    }

    /**
     * Load a model from a File or Blob.
     * @param {File|Blob} file
     * @param {object}    [opts]  — same options as loadModel
     * @returns {Promise<void>}
     */
    loadModelFromFile(file, opts = {}) {
        const url = URL.createObjectURL(file);
        return this.loadModel(url, opts).finally(() => URL.revokeObjectURL(url));
    }

    /**
     * Remove the current model from the scene.
     */
    clearModel() {
        if (this._model) {
            this._removeWireframe();
            this._originalMaterials.clear();
            this._scene.remove(this._model);
            this._model = null;
        }
    }

    /**
     * Reset camera to default position.
     */
    resetCamera() {
        this._camera.position.set(5, 5, 5);
        this._orbit.target.set(0, 0, 0);
        this._orbit.update();
    }

    /**
     * Completely tear down the viewport and free GPU resources.
     */
    dispose() {
        this._disposed = true;
        if (this._animationId) cancelAnimationFrame(this._animationId);
        this._resizeObserver?.disconnect();
        this.clearModel();
        this._renderer.dispose();
        this._orbit.dispose();
        this._wrapper.remove();
    }

    /* ------------------------------------------------------------------ */
    /*  Internals                                                          */
    /* ------------------------------------------------------------------ */

    _setModel(scene) {
        const isFirstLoad = !this._modelScale;
        this.clearModel();
        this._model = scene;
        this._scene.add(this._model);

        // Compute bounding box
        const box = new THREE.Box3().setFromObject(this._model);
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());

        // Lock scale on first load, reuse for subsequent loads
        if (isFirstLoad) {
            this._modelScale = 5 / Math.max(size.x, size.y, size.z);
        }

        // Apply scale and center at origin
        this._model.scale.setScalar(this._modelScale);
        this._model.position.copy(center).negate().multiplyScalar(this._modelScale);

        // Only reset camera on first load
        if (isFirstLoad) {
            this.resetCamera();
        }

        // Apply current wireframe state
        if (this._wireframeEnabled) this._addWireframe(this._model);

        // Apply current HDRI state (Lambert if off)
        if (!this._hdriEnabled) this._switchToLambert(this._model);
    }
}
