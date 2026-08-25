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
    /** How long to wait for a session to become ready before warning. */
    static STARTUP_TIMEOUT_MS = 5 * 60 * 1000;

    /**
     * Digital-asset extensions accepted by the loader. Houdini varies the
     * suffix by license tier - lc = Limited Commercial (Indie), nc =
     * Non-Commercial (Apprentice) - and otl is the pre-H12 spelling. The
     * backend installs whatever it is handed, so the file picker should not
     * be narrower than hou.hda.installFile().
     */
    static HDA_EXTENSIONS = ['.hda', '.hdalc', '.hdanc', '.otl', '.otllc', '.otlnc'];

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
        this._exportOutputs = [];         // outputs advertised by the loaded HDA
        this._exportLoaderMessage = null; // indicator text while an export cooks
        this._startupWatchdog = null;
        this._sessionEverReady = false;
        this._cookRequestedAt = null;

        // Cooking control (menu bar)
        this._cookEnabled = true;
        this._cookMode = 'auto';          // 'auto' | 'mouseup'
        this._cookInFlight = false;
        this._queuedCook = null;
        this._cookPending = false;        // parameters changed while paused
        this._lastSentValues = new Map();

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
            geometryLoaderText:  $('geometryLoaderText'),
            cookEnabled:         $('cookEnabled'),
            cookMode:            $('cookMode'),
            cookControls:        $('cookControls'),
            geometryInfo:        $('geometryInfo'),
            pointCount:          $('pointCount'),
            primCount:           $('primCount'),
            triCount:            $('triCount'),
            geoSize:             $('geoSize'),
            statCook:            $('statCook'),
            statExport:          $('statExport'),
            statUpload:          $('statUpload'),
            statDownload:        $('statDownload'),
            statParse:           $('statParse'),
            statSetup:           $('statSetup'),
            statDraw:            $('statDraw'),
            statToScreen:        $('statToScreen'),
            statTotal:           $('statTotal'),
            loadingText:         $('loadingText'),
            loadingSection:      $('loadingSection'),
            emptyState:          $('emptyState'),
            parametersSection:   $('parametersSection'),
            parametersContainer: $('parametersContainer'),
            menuBar:             $('menuBar'),
            menuStatus:          $('menuStatus'),
            menuHdaName:         $('menuHdaName'),
            menuHoudiniVersion:  $('menuHoudiniVersion'),
            compatWarning:       $('compatWarning'),
            menuLoadHDABtn:      $('menuLoadHDABtn'),
            menuTerminateBtn:    $('menuTerminateBtn'),
            menuExportBtn:       $('menuExportBtn'),
            exportModal:         $('exportModal'),
            exportOutputSelect:  $('exportOutputSelect'),
            exportConfirmBtn:    $('exportConfirmBtn'),
            exportCancelBtn:     $('exportCancelBtn'),
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

        // Cook on/off, and when changes trigger a cook
        this._el.cookEnabled?.addEventListener('change', (e) =>
            this._setCookEnabled(e.target.checked));
        this._el.cookMode?.addEventListener('change', (e) =>
            this._setCookMode(e.target.value));
        this._updateCookModeUI();

        // Log console toggle
        this._el.logConsole?.querySelector('.log-console-header')
            ?.addEventListener('click', () => this._toggleLogConsole());

        // The cook controls sit in that header — don't let their clicks toggle it
        this._el.cookControls?.addEventListener('click', (e) => e.stopPropagation());

        // Export dialog
        this._el.exportConfirmBtn?.addEventListener('click', () => this._confirmExport());
        this._el.exportCancelBtn?.addEventListener('click', () => this._hideExportModal());
        this._el.exportModal?.addEventListener('click', (e) => {
            // Click on the backdrop, not the dialog itself, dismisses it.
            if (e.target === this._el.exportModal) this._hideExportModal();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this._hideExportModal();
        });

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
            this._startStartupWatchdog();
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
     * Upload and load a new digital asset into the active session.
     * @param {File} file — a Houdini digital asset (see AuroraApp.HDA_EXTENSIONS)
     */
    async loadHDA(file) {
        if (!file) return;
        this._hideCompatWarning();
        const name = file.name.toLowerCase();
        if (!AuroraApp.HDA_EXTENSIONS.some(ext => name.endsWith(ext))) {
            alert('Invalid file type. Please use a Houdini digital asset ('
                + AuroraApp.HDA_EXTENSIONS.join(', ') + ').');
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
     * Export scene geometry as a GLB download.
     *
     * An asset can expose more than one output — typically light preview
     * geometry on output 0 for the viewport, and heavier delivery geometry on
     * a later one. When it does, ask which output to save; otherwise there is
     * nothing to choose and the export runs straight away.
     */
    exportScene() {
        if (!this._currentGeometryUrl) {
            alert('No geometry available to export.');
            return;
        }

        if (this._exportOutputs.length > 1) {
            this._showExportModal();
            return;
        }

        this._requestExport(0);
    }

    /**
     * @private — ask the session for one output, to be saved rather than drawn.
     * @param {number} outputIndex
     */
    _requestExport(outputIndex) {
        const output = this._exportOutputs[outputIndex];
        const label = output ? ` (${output.label})` : '';
        this._addLog('info',
            `Exporting output ${outputIndex}${label} — requesting download URL...`,
            'Client');

        // An export output has usually never cooked before, so this can take
        // far longer than a viewport recook. Say what is happening, and which
        // output it is happening to.
        this._exportLoaderMessage = this._exportOutputs.length > 1
            ? `Cooking output ${outputIndex}${this._shortLabel(output)} for export...`
            : 'Cooking geometry for export...';
        this._showGeometryLoader(this._exportLoaderMessage, { trackTiming: false });

        this._pendingSave = true;
        this._session.requestGeometry({ purpose: 'save', output_index: outputIndex });
    }

    /**
     * @private — an output label in parentheses, short enough to keep the
     * loader overlay on one line.
     */
    _shortLabel(output) {
        if (!output?.label) return '';
        const label = output.label.length > 28
            ? `${output.label.slice(0, 27)}…`
            : output.label;
        return ` (${label})`;
    }

    /** @private — populate and open the output picker. */
    _showExportModal() {
        const select = this._el.exportOutputSelect;
        if (!select || !this._exportOutputs.length) {
            // Nothing to choose between — fall back to the viewport output.
            this._requestExport(0);
            return;
        }

        select.innerHTML = '';
        for (const output of this._exportOutputs) {
            const opt = document.createElement('option');
            opt.value = String(output.index);
            opt.textContent = `${output.index} — ${output.label}`;
            select.appendChild(opt);
        }

        // Default to the last output: an asset that bothers to expose several
        // is almost always keeping its deliverable on the final one.
        select.value = String(this._exportOutputs[this._exportOutputs.length - 1].index);

        this._el.exportModal?.classList.remove('hidden');
        select.focus();
    }

    /** @private */
    _hideExportModal() {
        this._el.exportModal?.classList.add('hidden');
    }

    /** @private */
    _confirmExport() {
        const raw = this._el.exportOutputSelect?.value;
        const index = Number.parseInt(raw, 10);
        this._hideExportModal();
        this._requestExport(Number.isNaN(index) ? 0 : index);
    }

    /* ================================================================== */
    /*  UI state management                                                */
    /* ================================================================== */

    /** @private */
    _showLanding() {
        this._hideCompatWarning();
        this._setHoudiniVersion('');
        this._el.landing?.classList.remove('hidden');
        this._el.app?.classList.add('hidden');
        if (this._el.uploadError) {
            this._el.uploadError.textContent = '';
            this._el.uploadError.classList.remove('visible');
        }
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
            this._paramUI.on('change', (evt) => this._onParameterChange(evt));
            this._paramUI.on('fileselect', ({ paramPath, file }) => {
                this._uploadParameterFile(paramPath, file);
            });
        }
    }

    /**
     * @private — a file parameter got a file: upload it, then set the
     * parameter to the uploaded key so the session can fetch it.
     */
    async _uploadParameterFile(paramPath, file) {
        this._paramUI.setFileStatus(paramPath, `Uploading ${file.name}…`, 'busy');
        this._addLog('info', `Uploading ${file.name} for ${paramPath}`, 'Client');

        const asset = await this._session.uploadAsset(file);
        if (!asset) {
            this._paramUI.setFileStatus(paramPath, `Upload failed — ${file.name}`, 'error');
            return;
        }

        this._paramUI.setFileStatus(paramPath, file.name, 'ok');
        this._applyParameter(paramPath, file.name, 1, { assetKey: asset.s3_key });
    }

    /** @private */
    _showSessionReady() {
        this._sessionEverReady = true;
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

    /**
     * @private
     * Backstop for a session that never becomes ready.
     *
     * The instance normally reports its own startup failures (see
     * websocket_handler.watch_for_startup_failure), but if it dies without
     * saying so — or never boots at all — the browser would otherwise sit on
     * the loading screen indefinitely. Warn the user instead.
     */
    _startStartupWatchdog() {
        this._clearStartupWatchdog();
        this._sessionEverReady = false;
        this._startupWatchdog = setTimeout(() => {
            this._startupWatchdog = null;
            if (this._sessionEverReady) return;
            this._showSessionFailure(
                `The session did not start within ${Math.round(AuroraApp.STARTUP_TIMEOUT_MS / 60000)} minutes. ` +
                'The instance may have failed to boot, or Houdini could not acquire a license. ' +
                'Check the /aws/ec2/aurora-session CloudWatch log group for details.'
            );
        }, AuroraApp.STARTUP_TIMEOUT_MS);
    }

    /** @private */
    _clearStartupWatchdog() {
        if (this._startupWatchdog) {
            clearTimeout(this._startupWatchdog);
            this._startupWatchdog = null;
        }
    }

    /**
     * @private
     * Abandon the session and return the user to the landing screen,
     * showing why. Used both for a session that never came up and for one
     * that dies after it was running.
     * @param {string} reason — human-readable explanation.
     */
    _showSessionFailure(reason) {
        this._clearStartupWatchdog();
        const message = reason || 'The Houdini session failed to start.';

        console.error('[AuroraApp] Session startup failed:', message);
        this._addLog('error', message, 'Session');

        this._teardownModules();
        this._showLanding();

        if (this._el.uploadError) {
            this._el.uploadError.textContent = message;
            this._el.uploadError.classList.add('visible');
        }
        if (this._el.initializeBtn) this._el.initializeBtn.disabled = false;
        this._setStatus('Session failed');
    }

    /** @private */
    _setStatus(text) {
        if (this._el.menuStatus) this._el.menuStatus.textContent = text;
    }

    /** @private Display which Houdini build the session instance runs. */
    _setHoudiniVersion(version) {
        if (!this._el.menuHoudiniVersion) return;
        this._el.menuHoudiniVersion.textContent = version ? `Houdini ${version}` : '';
        this._el.menuHoudiniVersion.title = version
            ? `The session instance is running Houdini ${version}`
            : '';
    }

    /**
     * @private
     * Warn that the loaded asset does not match the server's Houdini build.
     *
     * Houdini reports this as "incomplete asset definition" and "skipping
     * unrecognized parameter" warnings while installing. The asset still
     * loads, but may not cook as its author intended, so say so rather than
     * letting the user wonder why the result looks wrong.
     */
    _showCompatWarning(warning) {
        const el = this._el.compatWarning;
        if (!el || !warning) return;

        el.textContent = '';

        const text = document.createElement('div');
        text.textContent = warning.message
            || 'This asset does not match the Houdini version on the server.';
        el.appendChild(text);

        if (Array.isArray(warning.samples) && warning.samples.length) {
            const list = document.createElement('ul');
            for (const sample of warning.samples) {
                const li = document.createElement('li');
                li.textContent = sample;
                list.appendChild(li);
            }
            el.appendChild(list);
        }

        el.classList.remove('hidden');
        this._addLog('warning', warning.message || 'Asset/Houdini version mismatch', 'Session');
    }

    /** @private */
    _hideCompatWarning() {
        if (!this._el.compatWarning) return;
        this._el.compatWarning.textContent = '';
        this._el.compatWarning.classList.add('hidden');
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

    /**
     * @private
     * @param {string}  [message]  — what the session is busy doing
     * @param {object}  [opts]
     * @param {boolean} [opts.trackTiming=true] — start the round-trip clock the
     *                  statistics readout reports. Export cooks pass false:
     *                  they never reach the viewport, so counting them would
     *                  put an unrelated duration in front of the next cook.
     */
    _showGeometryLoader(message = 'Houdini is cooking...', opts = {}) {
        const { trackTiming = true } = opts;
        if (trackTiming) this._cookRequestedAt = performance.now();
        if (this._el.geometryLoaderText) {
            this._el.geometryLoaderText.textContent = message;
        }
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
    /*  Cooking control                                                    */
    /* ================================================================== */

    /**
     * A parameter control changed. `live` is true mid-drag; in "On Mouse Up"
     * mode those are ignored and the control's commit event does the cooking.
     * @private
     */
    _onParameterChange({ paramPath, value, numComponents, live }) {
        if (live && this._cookMode !== 'auto') return;
        this._applyParameter(paramPath, value, numComponents);
    }

    /**
     * Send a parameter to the session, cooking unless cooking is paused.
     * @private
     */
    _applyParameter(paramPath, value, numComponents, opts = {}) {
        // A drag emits the same value repeatedly at the ends of its range.
        const signature = JSON.stringify(value);
        if (!opts.assetKey && this._lastSentValues.get(paramPath) === signature) return;
        this._lastSentValues.set(paramPath, signature);

        if (!this._cookEnabled) {
            // Keep the session's parameters in sync, but do not cook. Re-enabling
            // "Cook" is what brings the viewport back up to date.
            this._cookPending = true;
            this._session.updateParameter(paramPath, value, numComponents,
                { ...opts, skipExport: true });
            return;
        }

        if (this._cookInFlight) {
            // A drag outruns the cook — keep only the newest value.
            this._queuedCook = { paramPath, value, numComponents, opts };
            return;
        }

        this._cookInFlight = true;
        this._showGeometryLoader();
        this._session.updateParameter(paramPath, value, numComponents, opts);
    }

    /** @private — send whatever a drag queued up behind the last cook. */
    _flushQueuedCook() {
        const queued = this._queuedCook;
        this._queuedCook = null;
        if (!queued || !this._cookEnabled) return;

        this._cookInFlight = true;
        this._showGeometryLoader();
        this._session.updateParameter(
            queued.paramPath, queued.value, queued.numComponents, queued.opts);
    }

    /** @private */
    _setCookEnabled(enabled) {
        this._cookEnabled = enabled;
        if (this._el.cookEnabled) this._el.cookEnabled.checked = enabled;
        this._el.cookEnabled?.closest('.menu-checkbox')
            ?.classList.toggle('cook-off', !enabled);

        this._addLog('info',
            enabled ? 'Cooking resumed' : 'Cooking paused — parameter changes will not recook',
            'Client');

        // Parameters changed while paused are already set on the session, so a
        // plain geometry request is enough to catch the viewport up.
        if (enabled && this._cookPending && !this._cookInFlight) {
            this._cookPending = false;
            this._cookInFlight = true;
            this._showGeometryLoader();
            this._session.requestGeometry();
        }
    }

    /** @private */
    _setCookMode(mode) {
        this._cookMode = mode === 'mouseup' ? 'mouseup' : 'auto';
        this._updateCookModeUI();
        this._addLog('info',
            `Cooking mode: ${this._cookMode === 'auto' ? 'Auto' : 'On Mouse Up'}`,
            'Client');
    }

    /** @private */
    _updateCookModeUI() {
        if (this._el.cookMode) this._el.cookMode.value = this._cookMode;
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

    /**
     * @private
     * @param {string} url
     * @param {object} [serverTimings] — the `timings` block from geometry_ready
     */
    _loadGeometry(url, serverTimings) {
        if (!this._viewport) {
            console.warn('[AuroraApp] Viewport not initialised');
            return;
        }
        const resetView = this._pendingNewHDA;
        this._pendingNewHDA = false;

        const requestedAt = this._cookRequestedAt;
        this._cookRequestedAt = null;

        // Fetch first and hand the blob to the viewport, so download time is
        // measured on its own instead of hiding inside the GLTF parse.
        const start = performance.now();
        fetch(url)
            .then((resp) => {
                if (!resp.ok) throw new Error(`Download failed: ${resp.status}`);
                return resp.blob();
            })
            .then((blob) => {
                const downloadMs = performance.now() - start;
                return this._viewport.loadModelFromFile(blob, { resetView })
                    .then(stats => ({ ...stats, downloadMs, bytes: blob.size }));
            })
            .then((stats) => {
                this._reportGeometryStats(stats, serverTimings, requestedAt);
                this._emit('geometry:loaded', { url, stats });
            })
            .catch(err => console.error('[AuroraApp] Error loading geometry:', err));
    }

    /**
     * Log and display the per-stage cost of the geometry that just arrived.
     * Server stages come from the session; client stages are measured here.
     * @private
     */
    _reportGeometryStats(stats, serverTimings, requestedAt) {
        const t = serverTimings || {};
        const ms = (v) => (Number.isFinite(v) ? `${(v / 1000).toFixed(2)}s` : '-');
        const sec = (v) => (Number.isFinite(v) ? `${v.toFixed(2)}s` : '-');

        const total = Number.isFinite(requestedAt)
            ? ms(performance.now() - requestedAt)
            : '-';

        // Everything between the download starting and the model's first frame.
        // Excludes only the wait for the next animation frame (~one frame).
        const toScreen = stats.downloadMs + stats.parseMs + stats.setupMs + stats.drawMs;

        const row = {
            statCook:     sec(t.cook_s),
            statExport:   sec(t.export_s),
            statUpload:   sec(t.upload_s),
            statDownload: ms(stats.downloadMs),
            statParse:    ms(stats.parseMs),
            statSetup:    ms(stats.setupMs),
            statDraw:     ms(stats.drawMs),
            statToScreen: ms(toScreen),
            statTotal:    total,
            triCount:     this._fmtCount(stats.triangles),
            geoSize:      this._fmtBytes(stats.bytes),
        };
        for (const [id, value] of Object.entries(row)) {
            if (this._el[id]) this._el[id].textContent = value;
        }

        this._addLog('info',
            `Geometry displayed in ${total} — cook ${row.statCook} · ` +
            `export ${row.statExport} · upload ${row.statUpload} · ` +
            `download ${row.statDownload} · parse ${row.statParse} · ` +
            `setup ${row.statSetup} · draw ${row.statDraw} ` +
            `[S3→screen ${row.statToScreen}] (${row.triCount} tris, ${row.geoSize})`,
            'Timing');
    }

    /** @private */
    _resetGeometryStats() {
        const ids = ['pointCount', 'primCount', 'triCount', 'geoSize', 'statCook',
                     'statExport', 'statUpload', 'statDownload', 'statParse',
                     'statSetup', 'statDraw', 'statToScreen', 'statTotal'];
        ids.forEach(id => { if (this._el[id]) this._el[id].textContent = '-'; });
    }

    /** @private */
    _fmtBytes(bytes) {
        if (!Number.isFinite(bytes)) return '-';
        if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
        if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${bytes} B`;
    }

    /** @private */
    _fmtCount(n) {
        if (!Number.isFinite(n)) return '-';
        if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
        if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
        return `${Math.round(n)}`;
    }

    /** @private */
    async _downloadGeometry(url, outputIndex) {
        // The cook is done but the export is not: a heavy output can take a
        // while to come down off S3, and the browser shows nothing until the
        // save dialog appears. Runs before the first await, so the loader
        // never blinks off between the cook and the download.
        this._showGeometryLoader('Downloading export...', { trackTiming: false });
        try {
            this._addLog('info', 'Downloading geometry...', 'Client');
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`Download failed: ${resp.status}`);

            const blob = await resp.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            const suffix = Number.isInteger(outputIndex) ? `_out${outputIndex}` : '';
            a.download = `geometry${suffix}_${Date.now()}.glb`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(blobUrl);

            this._addLog('info', 'Geometry saved to disk', 'Client');
        } catch (err) {
            console.error('[AuroraApp] Error saving geometry:', err);
            alert('Failed to save geometry: ' + err.message);
        } finally {
            this._hideGeometryLoader();
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
            this._clearStartupWatchdog();
            this._addLog('system', 'Houdini session ready', 'Client');
            this._showSessionReady();
            this._emit('session:ready');
        });

        s.on('session_info', (info) => {
            if (info.houdini_version) {
                this._setHoudiniVersion(info.houdini_version);
                this._addLog('system', `Server running Houdini ${info.houdini_version}`, 'Session');
            }
        });

        s.on('compatibility_warning', (warning) => {
            this._showCompatWarning(warning);
        });

        s.on('parameters_ready', (data) => {
            if (data.houdini_version) this._setHoudiniVersion(data.houdini_version);
            const paramCount = Object.keys(data.parameters?.parameters || {}).length;
            this._addLog('info',
                `Parameters extracted from HDA (${paramCount} params, ${data.node_count} nodes)`,
                'Client');
            this._pendingNewHDA = true;
            this._exportOutputs = Array.isArray(data.outputs) ? data.outputs : [];
            this._lastSentValues.clear();
            this._queuedCook = null;
            this._cookInFlight = false;
            this._cookPending = false;
            // The session exports the initial geometry as soon as it has sent
            // the parameters, so this is where that first round trip starts.
            this._cookRequestedAt = performance.now();

            // Reset geometry state
            this._currentGeometryUrl = null;
            this._setMenuEnabled('export', false);
            if (this._el.geometryInfo) this._el.geometryInfo.style.display = 'none';
            this._resetGeometryStats();
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
            this._cookInFlight = false;

            // Viewport cooks and export requests come back on the same event,
            // and a slider drag can land one in between the two halves of an
            // export. Route on the purpose the session echoes back rather than
            // on local state, so a stray cook is never saved to disk — falling
            // back to the flag for a session that does not send one.
            const isSave = geo.purpose === 'save'
                || (geo.purpose === undefined && this._pendingSave);

            if (geo.error) {
                this._addLog('error', `Geometry export failed: ${geo.error}`, 'Houdini');
                if (isSave) {
                    this._pendingSave = false;
                    this._exportLoaderMessage = null;
                }
                this._queuedCook = null;
                alert('Geometry export error: ' + geo.error);
                return;
            }

            if (geo.url) {
                this._currentGeometryUrl = geo.url;
                this._setMenuEnabled('export', true);

                if (isSave) {
                    this._pendingSave = false;
                    this._exportLoaderMessage = null;
                    this._downloadGeometry(geo.url, geo.output_index);
                } else {
                    this._loadGeometry(geo.url, geo.timings);

                    // A cook already in flight when Export was pressed answers
                    // first. Put the export indicator back — that work is still
                    // running, and the hide at the top of this handler took it
                    // down.
                    if (this._pendingSave && this._exportLoaderMessage) {
                        this._showGeometryLoader(this._exportLoaderMessage,
                            { trackTiming: false });
                    }
                }

                const from = Number.isInteger(geo.output_index)
                    ? ` from output ${geo.output_index}` +
                      (geo.output_label ? ` (${geo.output_label})` : '')
                    : '';
                this._addLog('info',
                    `Geometry ready${from}: ${geo.point_count} points, ` +
                    `${geo.primitive_count} primitives`,
                    'Houdini');
            }

            if (this._el.geometryInfo) this._el.geometryInfo.style.display = 'block';
            if (this._el.pointCount) this._el.pointCount.textContent = geo.point_count || '-';
            if (this._el.primCount) this._el.primCount.textContent = geo.primitive_count || '-';

            this._emit('geometry:ready', geo);
            this._flushQueuedCook();
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

        s.on('fatal_error', (err) => {
            this._hideGeometryLoader();
            this._showSessionFailure(err);
        });

        s.on('error', (err) => {
            this._hideGeometryLoader();

            // A failure before the session ever came up is fatal: the
            // instance is gone (or going). Return to the landing screen with
            // the reason rather than pretending the session is usable.
            if (!this._sessionEverReady) {
                this._showSessionFailure(err);
                return;
            }

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
        this._clearStartupWatchdog();
        if (this._session)  { this._session.dispose();  this._session = null; }
        if (this._viewport) { this._viewport.dispose();  this._viewport = null; }
        if (this._paramUI)  { this._paramUI.dispose();   this._paramUI = null; }
        this._currentGeometryUrl = null;
        this._pendingSave = false;
        this._pendingNewHDA = false;
        this._exportOutputs = [];
        this._exportLoaderMessage = null;
        this._hideExportModal();
    }
}
