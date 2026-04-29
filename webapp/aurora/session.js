/**
 * Aurora Session — modular WebSocket session client for Houdini Aurora.
 *
 * Manages the WebSocket connection to the Aurora backend, session lifecycle,
 * command dispatch, and HDA file uploads via S3 presigned URLs.
 *
 * Usage:
 *   import { AuroraSession } from './aurora/session.js';
 *
 *   const session = new AuroraSession({ url: CONFIG.websocket_url });
 *
 *   session.on('connected',        ()    => { ... });
 *   session.on('session_ready',    ()    => { ... });
 *   session.on('parameters_ready', data  => { ... });
 *   session.on('geometry_ready',   data  => { ... });
 *   session.on('status',           text  => { ... });  // human-readable status
 *   session.on('log',              entry => { ... });
 *   session.on('error',            err   => { ... });
 *   session.on('idle_warning',     data  => { ... });
 *   session.on('idle_timeout',     data  => { ... });
 *   session.on('terminated',       ()    => { ... });
 *
 *   await session.connect();
 *   session.startSession({ idle_timeout_minutes: 15 });
 *   await session.uploadHDA(file);
 *   session.updateParameter(paramPath, value, numComponents);
 *   session.requestGeometry({ purpose: 'save' });
 *   session.terminate();
 *   session.dispose();
 */

import { EventEmitter } from './events.js';

export class AuroraSession extends EventEmitter {
    /**
     * @param {object} opts
     * @param {string} opts.url              WebSocket endpoint URL (wss://…)
     * @param {number} [opts.connectTimeout=5000]   ms before connect gives up
     * @param {number} [opts.uploadTimeout=10000]   ms before upload-URL request gives up
     */
    constructor(opts = {}) {
        super();
        if (!opts.url) throw new Error('[AuroraSession] opts.url is required');

        this._url = opts.url;
        this._connectTimeout = opts.connectTimeout ?? 5000;
        this._uploadTimeout = opts.uploadTimeout ?? 10000;

        /** @type {WebSocket|null} */
        this._ws = null;

        /** @type {string|null} */
        this.sessionId = null;
    }

    /* ================================================================== */
    /*  Connection                                                         */
    /* ================================================================== */

    /**
     * Open the WebSocket and wait for a session ID from the backend.
     * Resolves once the session ID has been received.
     * @returns {Promise<void>}
     */
    connect() {
        return new Promise((resolve, reject) => {
            const ws = new WebSocket(this._url);

            ws.onopen = () => {
                this._ws = ws;

                // Wait for session_id handshake
                const handler = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.session_id) {
                        this.sessionId = data.session_id;
                        ws.removeEventListener('message', handler);

                        // Install permanent message handler
                        ws.onmessage = (event) => {
                            this._handleMessage(JSON.parse(event.data));
                        };

                        this._emit('connected', { sessionId: this.sessionId });
                        resolve();
                    }
                };

                ws.addEventListener('message', handler);
                ws.send(JSON.stringify({ action: 'get_session_id' }));
            };

            ws.onerror = () => reject(new Error('WebSocket connection failed'));

            setTimeout(() => reject(new Error('Connection timeout')), this._connectTimeout);
        });
    }

    /**
     * True when the WebSocket is open and ready to send.
     */
    get connected() {
        return this._ws?.readyState === WebSocket.OPEN;
    }

    /* ================================================================== */
    /*  Commands                                                           */
    /* ================================================================== */

    /**
     * Send an arbitrary JSON command over the WebSocket.
     * @param {object} command
     */
    send(command) {
        if (!this.connected) {
            console.warn('[AuroraSession] WebSocket not connected');
            return;
        }
        this._ws.send(JSON.stringify(command));
    }

    /**
     * Ask the backend to start a Houdini session on EC2.
     * @param {object} [opts]
     * @param {number} [opts.idle_timeout_minutes=15]
     * @param {number} [opts.idle_warning_minutes=2]
     */
    startSession(opts = {}) {
        this.send({
            action: 'start_session',
            idle_timeout_minutes: opts.idle_timeout_minutes ?? 15,
            idle_warning_minutes: opts.idle_warning_minutes ?? 2
        });
        this._emit('status', 'Starting EC2 instance…');
    }

    /**
     * Upload an HDA file to S3 via a presigned URL, then tell the
     * backend to extract its parameters.
     *
     * @param {File} file — the .hda file chosen by the user.
     * @returns {Promise<boolean>} true on success
     */
    async uploadHDA(file) {
        try {
            // 1. Request presigned URL from the backend
            const urlData = await this._requestUploadUrl(file);

            // 2. PUT the file to S3
            const uploadResponse = await fetch(urlData.upload_url, {
                method: 'PUT',
                body: file,
                headers: { 'Content-Type': file.type || 'application/octet-stream' }
            });

            if (!uploadResponse.ok) {
                throw new Error(`Upload failed: ${uploadResponse.status} ${uploadResponse.statusText}`);
            }

            this._emit('log', { level: 'info', message: `HDA uploaded to S3: ${file.name}`, context: 'Client' });

            // 3. Tell the backend to load it
            this.send({
                action: 'extract_parameters',
                filename: file.name,
                s3_key: urlData.s3_key
            });

            return true;
        } catch (error) {
            console.error('[AuroraSession] Error uploading HDA:', error);
            this._emit('log', { level: 'error', message: `Failed to upload HDA: ${error.message}`, context: 'Client' });
            this._emit('error', error.message);
            return false;
        }
    }

    /**
     * Send a parameter update to the Houdini session.
     * @param {string}       paramPath
     * @param {*}            value
     * @param {number}       [numComponents=1]
     */
    updateParameter(paramPath, value, numComponents = 1) {
        this.send({
            action: 'update_parameter',
            param: paramPath,
            value,
            num_components: numComponents
        });
    }

    /**
     * Request geometry from the Houdini session.
     * @param {object} [opts]
     * @param {string} [opts.purpose] — e.g. 'save'
     */
    requestGeometry(opts = {}) {
        this.send({ action: 'get_geometry', ...opts });
    }

    /**
     * Send a terminate command and close the WebSocket.
     */
    terminate() {
        this.send({ action: 'terminate_session' });
        this._emit('status', 'Terminating…');

        // Give the backend a moment, then close locally
        setTimeout(() => this._close(), 1000);
    }

    /**
     * Full teardown — close the WebSocket and remove all listeners.
     */
    dispose() {
        this._close();
        this.removeAllListeners();
    }

    /* ================================================================== */
    /*  Internal — message routing                                         */
    /* ================================================================== */

    /** @private */
    _handleMessage(data) {
        // Heartbeats are silent
        if (data.action === 'heartbeat') return;

        // Session ID (in case it arrives again)
        if (data.session_id && !this.sessionId) {
            this.sessionId = data.session_id;
        }

        // Route by action / status
        if (data.action === 'session_started') {
            this._emit('status', 'EC2 instance starting…');
        }

        if (data.status === 'ec2_connected') {
            this._emit('status', 'EC2 connected, loading Houdini…');
        }

        if (data.action === 'session_identified' || data.status === 'ready') {
            this._emit('session_ready');
        }

        if (data.action === 'parameters_ready') {
            this._emit('parameters_ready', data);
        }

        if (data.action === 'geometry_ready' && data.geometry) {
            this._emit('geometry_ready', data.geometry);
        }

        if (data.action === 'terminating') {
            this._emit('status', 'Terminated');
            this._emit('terminated', data);
        }

        if (data.action === 'idle_warning') {
            this._emit('idle_warning', data);
        }

        if (data.action === 'idle_timeout') {
            this._emit('idle_timeout', data);
        }

        if (data.action === 'log') {
            this._emit('log', { level: data.level, message: data.message, context: data.context });
        }

        if (data.error) {
            this._emit('error', data.error);
        }

        // Always emit the raw message for advanced consumers
        this._emit('message', data);
    }

    /* ================================================================== */
    /*  Internal helpers                                                   */
    /* ================================================================== */

    /** @private — ask backend for a presigned S3 upload URL */
    _requestUploadUrl(file) {
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(
                () => reject(new Error('Timeout waiting for upload URL')),
                this._uploadTimeout
            );

            const handler = (event) => {
                const data = JSON.parse(event.data);
                if (data.action === 'upload_url_ready') {
                    clearTimeout(timeout);
                    this._ws.removeEventListener('message', handler);
                    resolve(data);
                } else if (data.error) {
                    clearTimeout(timeout);
                    this._ws.removeEventListener('message', handler);
                    reject(new Error(data.error));
                }
            };

            this._ws.addEventListener('message', handler);

            this.send({
                action: 'request_upload_url',
                filename: file.name,
                content_type: file.type || 'application/octet-stream'
            });
        });
    }

    /** @private */
    _close() {
        if (this._ws) {
            this._ws.close();
            this._ws = null;
        }
        this.sessionId = null;
    }
}
