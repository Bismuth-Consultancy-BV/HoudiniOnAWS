/**
 * Aurora App — main application orchestrator.
 *
 * Ties together AuroraSession, AuroraViewport, and AuroraParameters
 * with the DOM to provide a complete Houdini-in-the-cloud experience.
 *
 * The app discovers DOM elements by ID convention and manages the full
 * session lifecycle, menu interactions, log console, and geometry flow.
 *
 * Usage:
 *   import { AuroraApp } from './aurora/app.js';
 *
 *   const app = new AuroraApp({
 *       websocket_url: 'wss://...',
 *       idle_timeout_minutes: 15,
 *       idle_warning_minutes: 2
 *   });
 *
 *   app.mount();   // bind DOM, show landing screen
 *
 * Events (subscribe via app.on()):
 *   'session:ready'       — Houdini session is connected and ready
 *   'parameters:ready'    — HDA parameters have been loaded (payload: data)
 *   'geometry:ready'      — New geometry received (payload: geo)
 *   'geometry:loaded'     — Geometry loaded into the viewport (payload: { url })
 *
 * Extending:
 *   Subclass AuroraApp and override any _show* or _wire* method to
 *   customise behaviour without touching the HTML template.
 */

import { EventEmitter } from './events.js';
import { AuroraSession } from './session.js';
import { AuroraViewport } from './viewport.js';
import { AuroraParameters } from './parameters.js';

export class AuroraApp extends EventEmitter {
    /**
     * @param {object} config
     * @param {string} config.websocket_url           WebSocket endpoint (wss://…)
     * @param {number} [config.idle_timeout_minutes=15]
     * @param {number} [config.idle_warning_minutes=2]
     */
    constructor(config = {}) {
        super();

        if (!config.websocket_url) {
            throw new Error('[AuroraApp] config.websocket_url is required');
        }

        this._config = config;

        // Module instances (created lazily)
        /** @type {AuroraSession|null} */
        this._session = null;
        /** @type {AuroraViewport|null} */
        this._viewport = null;
        /** @type {AuroraParameters|null} */
        this._paramUI = null;

        // State
        this._currentGeometryUrl = null;
        this._pendingSave = false;
        this._pendingNewHDA = false;

        // DOM references (populated by mount())
        this._el = {};
        this._mounted = false;
    }

    /* ================================================================== */
    /*  Lifecycle                                                          */
    /* ================================================================== */

    /**
     * Discover DOM elements, bind event listeners, and show the landing
     * screen. Call once after the DOM is ready.
     */
    mount() {
        if (this._mounted) return;
        this._mounted = true;

        this._bindElements();
        this._bindDOMEvents();
        this._showLanding();
    }

    /**
     * Full teardown — dispose all sub-modules, unbind listeners.
     */
    destroy() {
        this._teardownModules();
        this._mounted = false;
        this.removeAllListeners();
    }

    /* ================================================================== */
    /*  DOM binding                                                        */
    /* ================================================================== */

    /** @private Resolve all required DOM elements by ID. */
    _bindElements() {
        const $ = (id) => document.getElementById(id);

        this._el = {
            landing:             $('landingScreen'),
            app:                 $('appContainer'),
            initializeBtn:       $('initializeBtn'),
            uploadError:         $('uploadError'),
            hdaFileInput:        $('hdaFileInput'),
            viewerMount:         $('viewerMount'),
            geometryLoader:      $('geometryLoader'),
            geometryInfo:        $('geometryInfo'),
            pointCount:          $('pointCount'),
            primCount:           $('primCount'),
            loadingText:         $('loadingText'),
            loadingSection:      $('loadingSection'),
            emptyState:          $('emptyState'),
            parametersSection:   $('parametersSection'),
            parametersContainer: $('parametersContainer'),
            menuBar:             $('menuBar'),
            menuStatus:          $('menuStatus'),
            menuHdaName:         $('menuHdaName'),
            menuLoadHDABtn:      $('menuLoadHDABtn'),
            menuTerminateBtn:    $('menuTerminateBtn'),
            menuExportBtn:       $('menuExportBtn'),
            logConsole:          $('logConsole'),
            logMessages:         $('logMessages'),
        };
    }

    /** @private Attach all DOM event listeners. */
    _bindDOMEvents() {
        // Start session
        this._el.initializeBtn?.addEventListener('click', () => this.startSession());

        // HDA file chooser
        this._el.hdaFileInput?.addEventListener('change', (e) => this._onHDAFileSelected(e));

        // Menu bar — event delegation via data-action attributes
        this._el.menuBar?.addEventListener('click', (e) => this._onMenuBarClick(e));

        // Log console toggle
        this._el.logConsole?.querySelector('.log-console-header')
            ?.addEventListener('click', () => this._toggleLogConsole());

        // Close menus on outside click
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.menu-item')) this._closeAllMenus();
        });
    }

    /* ================================================================== */
    /*  Session lifecycle                                                  */
    /* ================================================================== */

    /**
     * Connect to the backend and start a new Houdini session.
     */
    async startSession() {
        this._el.initializeBtn.disabled = true;
        this._el.uploadError.textContent = '';

        this._showApp();
        this._updateLoadingMessage('Connecting to session...');
        this._setStatus('Connecting...');

        try {
            this._session = new AuroraSession({ url: this._config.websocket_url });
            this._wireSessionEvents();

            await this._session.connect();
            this._addLog('system', 'WebSocket connected', 'Client');

            this._session.startSession({
                idle_timeout_minutes: this._config.idle_timeout_minutes || 15,
                idle_warning_minutes: this._config.idle_warning_minutes || 2,
            });

            this._updateLoadingMessage('Starting EC2 instance...');
            this._setStatus('Starting...');
        } catch (error) {
            console.error('[AuroraApp] Failed to start session:', error);
            this._el.uploadError.textContent = 'Failed to connect. Please try again.';
            this._showLanding();
        }
    }

    /**
     * Terminate the current session and return to the landing screen.
     */
    async terminateSession() {
        if (!confirm('Are you sure you want to terminate this session?')) return;

        this._setStatus('Terminating...');
        this._session?.terminate();

        await new Promise(r => setTimeout(r, 1000));
        this._teardownModules();
        this._showLanding();
    }

    /**
     * Upload and load a new HDA file into the active session.
     * @param {File} file — a .hda file
     */
    async loadHDA(file) {
        if (!file) return;
        if (!file.name.endsWith('.hda')) {
            alert('Invalid file type. Please use a .hda (Houdini Digital Asset) file.');
            return;
        }

        this._showLoadingHDA();
        this._updateLoadingMessage('Uploading HDA file to S3...');
        this._addLog('info', `Loading HDA: ${file.name}`, 'Client');

        const ok = await this._session.uploadHDA(file);
        if (!ok) {
            alert('Failed to upload HDA file. Please try again.');
            this._showSessionReady();
        } else {
            this._updateLoadingMessage('Extracting parameters from HDA...');
        }
    }

    /**
     * Export the current scene geometry as a GLB download.
     */
    exportScene() {
        if (!this._currentGeometryUrl) {
            alert('No geometry available to export.');
            return;
        }
        this._addLog('info', 'Requesting fresh geometry download URL...', 'Client');
        this._session.requestGeometry({ purpose: 'save' });
        this._pendingSave = true;
    }

    /* ================================================================== */
    /*  UI state management                                                */
    /* ================================================================== */

    /** @private */
    _showLanding() {
        this._el.landing?.classList.remove('hidden');
        this._el.app?.classList.add('hidden');
        if (this._el.uploadError) this._el.uploadError.textContent = '';
        if (this._el.initializeBtn) this._el.initializeBtn.disabled = false;
        this._setStatus('Disconnected');
        this._setHdaName('');
    }

    /** @private */
    _showApp() {
        this._el.landing?.classList.add('hidden');
        this._el.app?.classList.remove('hidden');

        this._showSection('loading');
        this._setMenuEnabled('load', false);
        this._setMenuEnabled('terminate', false);
        this._setMenuEnabled('export', false);

        if (!this._viewport) {
            this._viewport = new AuroraViewport(this._el.viewerMount);
        }

        if (!this._paramUI) {
            this._paramUI = new AuroraParameters(this._el.parametersContainer);
            this._paramUI.on('change', ({ paramPath, value, numComponents }) => {
                this._showGeometryLoader();
                this._session.updateParameter(paramPath, value, numComponents);
            });
        }
    }

    /** @private */
    _showSessionReady() {
        this._showSection('empty');
        this._setStatus('Session Active');
        this._setMenuEnabled('load', true);
        this._setMenuEnabled('terminate', true);
        this._setMenuEnabled('export', false);
    }

    /** @private */
    _showLoadingHDA() {
        this._showSection('loading');
        this._setHdaName('');
    }

    /** @private */
    _showParameters() {
        this._showSection('parameters');
    }

    /**
     * Show one sidebar section and hide the others.
     * @param {'loading'|'empty'|'parameters'} section
     * @private
     */
    _showSection(section) {
        const map = {
            loading:    this._el.loadingSection,
            empty:      this._el.emptyState,
            parameters: this._el.parametersSection,
        };
        Object.entries(map).forEach(([key, el]) => {
            if (el) el.style.display = (key === section) ? 'block' : 'none';
        });
    }

    /** @private */
    _setStatus(text) {
        if (this._el.menuStatus) this._el.menuStatus.textContent = text;
    }

    /** @private */
    _setHdaName(text, title = '') {
        if (this._el.menuHdaName) {
            this._el.menuHdaName.textContent = text;
            this._el.menuHdaName.title = title;
        }
    }

    /**
     * Enable or disable a menu action button.
     * @param {'load'|'terminate'|'export'} action
     * @param {boolean} enabled
     * @private
     */
    _setMenuEnabled(action, enabled) {
        const btnMap = {
            load:      this._el.menuLoadHDABtn,
            terminate: this._el.menuTerminateBtn,
            export:    this._el.menuExportBtn,
        };
        const btn = btnMap[action];
        if (btn) btn.disabled = !enabled;
    }

    /** @private */
    _updateLoadingMessage(msg) {
        if (this._el.loadingText) this._el.loadingText.textContent = msg;
    }

    /** @private */
    _showGeometryLoader() {
        if (this._el.geometryLoader) this._el.geometryLoader.style.display = 'flex';
    }

    /** @private */
    _hideGeometryLoader() {
        if (this._el.geometryLoader) this._el.geometryLoader.style.display = 'none';
    }

    /* ================================================================== */
    /*  Menu handling (event delegation)                                   */
    /* ================================================================== */

    /** @private */
    _onMenuBarClick(e) {
        // Toggle dropdown when a menu-button is clicked
        const menuBtn = e.target.closest('.menu-button');
        if (menuBtn) {
            this._toggleMenu(menuBtn);
            return;
        }

        // Dispatch data-action buttons
        const actionEl = e.target.closest('[data-action]');
        if (actionEl) {
            this._closeAllMenus();
            const name = actionEl.dataset.action;

            switch (name) {
                case 'load-hda':
                    this._el.hdaFileInput?.click();
                    break;
                case 'terminate':
                    this.terminateSession();
                    break;
                case 'export':
                    this.exportScene();
                    break;
            }
        }
    }

    /** @private */
    _toggleMenu(btn) {
        const dropdown = btn.nextElementSibling;
        const wasOpen = dropdown?.classList.contains('open');
        this._closeAllMenus();
        if (!wasOpen) dropdown?.classList.add('open');
    }

    /** @private */
    _closeAllMenus() {
        document.querySelectorAll('.menu-dropdown').forEach(d => d.classList.remove('open'));
    }

    /* ================================================================== */
    /*  Log console                                                        */
    /* ================================================================== */

    /** @private */
    _toggleLogConsole() {
        const lc = this._el.logConsole;
        if (!lc) return;
        const toggle = lc.querySelector('.log-console-toggle');

        if (lc.classList.contains('collapsed')) {
            lc.classList.remove('collapsed');
            if (toggle) toggle.textContent = '▼';
        } else {
            lc.classList.add('collapsed');
            if (toggle) toggle.textContent = '▲';
        }
    }

    /**
     * Append a log entry to the console panel.
     * @param {string} level   — info | warning | error | fatal | system
     * @param {string} message
     * @param {string} [context]
     * @private
     */
    _addLog(level, message, context) {
        const container = this._el.logMessages;
        if (!container) return;

        const entry = document.createElement('div');
        entry.className = `log-entry log-${level}`;

        const ts = new Date().toLocaleTimeString();
        const ctx = context ? `[${context}] ` : '';
        entry.innerHTML =
            `<span class="log-time">${ts}</span>` +
            `<span class="log-level">${level.toUpperCase()}</span>` +
            `<span class="log-message">${ctx}${message}</span>`;

        container.appendChild(entry);
        container.scrollTop = container.scrollHeight;

        // Cap at 200 entries
        while (container.children.length > 200) {
            container.removeChild(container.firstChild);
        }
    }

    /* ================================================================== */
    /*  Geometry                                                           */
    /* ================================================================== */

    /** @private */
    _loadGeometry(url) {
        if (!this._viewport) {
            console.warn('[AuroraApp] Viewport not initialised');
            return;
        }
        const resetView = this._pendingNewHDA;
        this._pendingNewHDA = false;

        this._viewport.loadModel(url, { resetView })
            .then(() => this._emit('geometry:loaded', { url }))
            .catch(err => console.error('[AuroraApp] Error loading geometry:', err));
    }

    /** @private */
    async _downloadGeometry(url) {
        try {
            this._addLog('info', 'Downloading geometry...', 'Client');
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`Download failed: ${resp.status}`);

            const blob = await resp.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = `geometry_${Date.now()}.glb`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(blobUrl);

            this._addLog('info', 'Geometry saved to disk', 'Client');
        } catch (err) {
            console.error('[AuroraApp] Error saving geometry:', err);
            alert('Failed to save geometry: ' + err.message);
        }
    }

    /* ================================================================== */
    /*  HDA file input                                                     */
    /* ================================================================== */

    /** @private */
    _onHDAFileSelected(e) {
        const file = e.target.files[0];
        this._el.hdaFileInput.value = '';
        if (file) this.loadHDA(file);
    }

    /* ================================================================== */
    /*  Session event wiring                                               */
    /* ================================================================== */

    /** @private Wire all AuroraSession events to the app's UI logic. */
    _wireSessionEvents() {
        const s = this._session;

        s.on('status', (text) => {
            this._setStatus(text);
            this._updateLoadingMessage(text);
        });

        s.on('session_ready', () => {
            this._addLog('system', 'Houdini session ready', 'Client');
            this._showSessionReady();
            this._emit('session:ready');
        });

        s.on('parameters_ready', (data) => {
            const paramCount = Object.keys(data.parameters?.parameters || {}).length;
            this._addLog('info',
                `Parameters extracted from HDA (${paramCount} params, ${data.node_count} nodes)`,
                'Client');
            this._pendingNewHDA = true;

            // Reset geometry state
            this._currentGeometryUrl = null;
            this._setMenuEnabled('export', false);
            if (this._el.geometryInfo) this._el.geometryInfo.style.display = 'none';
            if (this._el.pointCount) this._el.pointCount.textContent = '-';
            if (this._el.primCount) this._el.primCount.textContent = '-';
            if (this._viewport) this._viewport.clearModel();

            // Build parameter UI
            this._paramUI.load(data.parameters);
            if (this._paramUI.toolLabel) {
                this._setHdaName(this._paramUI.toolLabel, this._paramUI.toolDescription);
            }
            this._showParameters();
            this._emit('parameters:ready', data);
        });

        s.on('geometry_ready', (geo) => {
            this._hideGeometryLoader();

            if (geo.error) {
                this._addLog('error', `Geometry export failed: ${geo.error}`, 'Houdini');
                if (this._pendingSave) this._pendingSave = false;
                alert('Geometry export error: ' + geo.error);
                return;
            }

            if (geo.url) {
                this._currentGeometryUrl = geo.url;
                this._setMenuEnabled('export', true);

                if (this._pendingSave) {
                    this._pendingSave = false;
                    this._downloadGeometry(geo.url);
                } else {
                    this._loadGeometry(geo.url);
                }

                this._addLog('info',
                    `Geometry ready: ${geo.point_count} points, ${geo.primitive_count} primitives`,
                    'Houdini');
            }

            if (this._el.geometryInfo) this._el.geometryInfo.style.display = 'block';
            if (this._el.pointCount) this._el.pointCount.textContent = geo.point_count || '-';
            if (this._el.primCount) this._el.primCount.textContent = geo.primitive_count || '-';

            this._emit('geometry:ready', geo);
        });

        s.on('idle_warning', (data) => {
            const minutes = Math.ceil(data.seconds_remaining / 60);
            this._setStatus(`⚠️ Idle - ${minutes} min left`);
            alert(data.message + ' Interact with parameters to keep session alive.');
        });

        s.on('idle_timeout', (data) => {
            this._setStatus('Timed Out');
            alert(data.message);
            setTimeout(() => {
                this._teardownModules();
                this._showLanding();
            }, 2000);
        });

        s.on('log', ({ level, message, context }) => {
            this._addLog(level, message, context);
        });

        s.on('error', (err) => {
            this._hideGeometryLoader();
            if (!this._el.app?.classList.contains('hidden')) {
                alert('Error: ' + err);
                this._showSessionReady();
            } else {
                if (this._el.uploadError) this._el.uploadError.textContent = err;
                if (this._el.initializeBtn) this._el.initializeBtn.disabled = false;
            }
        });
    }

    /* ================================================================== */
    /*  Teardown                                                           */
    /* ================================================================== */

    /** @private Dispose all sub-modules and reset state. */
    _teardownModules() {
        if (this._session)  { this._session.dispose();  this._session = null; }
        if (this._viewport) { this._viewport.dispose();  this._viewport = null; }
        if (this._paramUI)  { this._paramUI.dispose();   this._paramUI = null; }
        this._currentGeometryUrl = null;
        this._pendingSave = false;
        this._pendingNewHDA = false;
    }
}
