/**
 * Aurora Parameters — modular Houdini parameter interface builder.
 *
 * Translates a Houdini Digital Asset parameter schema into interactive
 * HTML controls (sliders, vectors, menus, colour pickers, etc.) and
 * emits events when the user changes a value.
 *
 * Usage:
 *   import { AuroraParameters } from './aurora/parameters.js';
 *
 *   const params = new AuroraParameters(document.getElementById('sidebar'));
 *
 *   // React to user changes
 *   params.on('change', ({ paramPath, value, numComponents }) => {
 *       sendToServer({ action: 'update_parameter', param: paramPath, value, num_components: numComponents });
 *   });
 *
 *   // Load a schema (as returned by the Houdini session)
 *   params.load(schema);
 *
 *   // Later, tear down
 *   params.dispose();
 *
 * Events:
 *   'change'  — { paramPath, value, numComponents }
 *   'load'    — { schema, paramCount }
 *
 * Extending:
 *   Subclass AuroraParameters and override createControl() to add or
 *   replace control types for your own UI framework.
 */

import { EventEmitter } from './events.js';

export class AuroraParameters extends EventEmitter {
    /**
     * @param {HTMLElement}  container — the DOM element that will hold the controls.
     * @param {object}       [opts]
     * @param {string}       [opts.headingTag='h3']  tag used for the section heading
     * @param {string}       [opts.heading='Parameters']  section heading text
     */
    constructor(container, opts = {}) {
        super();
        this._container = container;
        this._opts = { headingTag: 'h3', heading: 'Parameters', ...opts };

        /** @type {object|null} the raw schema last loaded */
        this.schema = null;

        /** Map of paramPath → DOM element */
        this._controls = {};
    }

    /* ================================================================== */
    /*  Public API                                                         */
    /* ================================================================== */

    /**
     * Build parameter controls from a Houdini parameter schema.
     *
     * @param {object} schema — the schema object returned by the session.
     *   Expected shape:
     *     {
     *       tool_name: string,
     *       tool_version?: string,
     *       description?: string,
     *       parameters: {
     *         [paramPath]: {
     *           name, type, default,
     *           num_components?,
     *           folder?,
     *           ui?: { control, label, min, max, step, options, multiline }
     *         }
     *       }
     *     }
     */
    load(schema) {
        this.clear();
        this.schema = schema;

        if (!schema?.parameters) {
            console.warn('[AuroraParameters] No parameters in schema');
            return;
        }

        let currentFolder = '';

        Object.entries(schema.parameters).forEach(([paramPath, paramDef]) => {
            // Folder dividers
            if (paramDef.folder && paramDef.folder !== currentFolder) {
                currentFolder = paramDef.folder;
                this._container.appendChild(this._createFolderDivider(currentFolder));
            }

            const control = this.createControl(paramPath, paramDef);
            if (control) {
                this._container.appendChild(control);
            }
        });

        const paramCount = Object.keys(schema.parameters).length;
        this._emit('load', { schema, paramCount });
    }

    /**
     * Remove all generated controls from the container.
     */
    clear() {
        this._container.innerHTML = '';
        this._controls = {};
        this.schema = null;
    }

    /**
     * Convenience: returns a formatted tool name + version string
     * from the last loaded schema, or '' if nothing is loaded.
     */
    get toolLabel() {
        if (!this.schema?.tool_name) return '';
        let label = this.schema.tool_name;
        if (this.schema.tool_version) label += ` v${this.schema.tool_version}`;
        return label;
    }

    /**
     * Convenience: tool description from the schema.
     */
    get toolDescription() {
        return this.schema?.description || '';
    }

    /**
     * Tear down.
     */
    dispose() {
        this.clear();
        this.removeAllListeners();
    }

    /* ================================================================== */
    /*  Control factory — override in subclasses to customise              */
    /* ================================================================== */

    /**
     * Create a single parameter control element.
     *
     * Override this method in a subclass to replace or extend the
     * controls (e.g. to use a different UI toolkit).
     *
     * @param {string} paramPath — unique path identifying the parameter.
     * @param {object} paramDef  — definition from the schema.
     * @returns {HTMLElement|null}
     */
    createControl(paramPath, paramDef) {
        const wrapper = document.createElement('div');
        wrapper.className = 'param-control';

        const label = document.createElement('label');
        label.textContent = paramDef.ui?.label || paramDef.name;

        let input = null;
        const controlType = paramDef.ui?.control || paramDef.type;

        switch (controlType) {
            case 'slider':       input = this._createFloat(paramPath, paramDef);        break;
            case 'vector3':      input = this._createVector(paramPath, paramDef, 3);    break;
            case 'vector4':      input = this._createVector(paramPath, paramDef, 4);    break;
            case 'number':       input = this._createInt(paramPath, paramDef);          break;
            case 'checkbox':     input = this._createCheckbox(paramPath, paramDef);     break;
            case 'select':       input = this._createMenu(paramPath, paramDef);         break;
            case 'text':         input = this._createString(paramPath, paramDef);       break;
            case 'color_picker': input = this._createColor(paramPath, paramDef);        break;
            case 'file_browser': input = this._createFile(paramPath, paramDef);         break;
            case 'button':       input = this._createButton(paramPath, paramDef);       break;
            default:
                // Fallback to type-based mapping
                switch (paramDef.type) {
                    case 'float':    input = this._createFloat(paramPath, paramDef);    break;
                    case 'int':      input = this._createInt(paramPath, paramDef);      break;
                    case 'checkbox': input = this._createCheckbox(paramPath, paramDef); break;
                    case 'menu':     input = this._createMenu(paramPath, paramDef);     break;
                    case 'string':   input = this._createString(paramPath, paramDef);   break;
                    default: return null;
                }
        }

        wrapper.appendChild(label);
        if (input) {
            wrapper.appendChild(input);
            this._controls[paramPath] = input;
        }

        return wrapper;
    }

    /* ================================================================== */
    /*  Individual control builders                                        */
    /* ================================================================== */

    /** @private */
    _emitChange(paramPath, value, numComponents) {
        this._emit('change', { paramPath, value, numComponents });
    }

    /** @private */
    _createFolderDivider(name) {
        const div = document.createElement('div');
        div.className = 'param-folder';
        div.innerHTML = `<strong>${name}</strong>`;
        div.style.marginTop = '12px';
        div.style.marginBottom = '4px';
        div.style.borderBottom = '1px solid #444';
        div.style.paddingBottom = '4px';
        return div;
    }

    /** @private — range slider for floats */
    _createFloat(paramPath, paramDef) {
        const container = document.createElement('div');

        const slider = document.createElement('input');
        slider.type = 'range';
        slider.min = paramDef.ui?.min ?? 0;
        slider.max = paramDef.ui?.max ?? 10;
        slider.step = paramDef.ui?.step ?? 0.01;
        slider.value = paramDef.default ?? 0;

        const stepVal = parseFloat(slider.step);
        const decimals = stepVal >= 1 ? 0 : Math.max(0, Math.ceil(-Math.log10(stepVal)));

        const valueSpan = document.createElement('span');
        valueSpan.className = 'param-value';
        valueSpan.textContent = parseFloat(slider.value).toFixed(decimals);

        slider.addEventListener('input', () => {
            valueSpan.textContent = parseFloat(slider.value).toFixed(decimals);
        });

        slider.addEventListener('change', () => {
            this._emitChange(paramPath, parseFloat(slider.value), paramDef.num_components || 1);
        });

        container.appendChild(slider);
        container.appendChild(valueSpan);
        container.dataset.paramPath = paramPath;
        return container;
    }

    /** @private — number spinner for integers */
    _createInt(paramPath, paramDef) {
        const container = document.createElement('div');

        const input = document.createElement('input');
        input.type = 'number';
        input.min = paramDef.ui?.min ?? 0;
        input.max = paramDef.ui?.max ?? 100;
        input.step = paramDef.ui?.step ?? 1;
        input.value = paramDef.default ?? 0;

        input.addEventListener('change', () => {
            this._emitChange(paramPath, parseInt(input.value), paramDef.num_components || 1);
        });

        container.appendChild(input);
        return container;
    }

    /** @private — multi-component numeric input (vec3 / vec4) */
    _createVector(paramPath, paramDef, components) {
        const container = document.createElement('div');
        container.className = 'vector-control';
        container.style.display = 'flex';
        container.style.gap = '4px';

        const axisLabels = ['X', 'Y', 'Z', 'W'];
        const defaults = Array.isArray(paramDef.default) ? paramDef.default : [0, 0, 0, 0];
        const inputs = [];

        for (let i = 0; i < components; i++) {
            const wrap = document.createElement('div');
            wrap.style.flex = '1';

            const lbl = document.createElement('span');
            lbl.textContent = axisLabels[i];
            lbl.style.fontSize = '10px';
            lbl.style.opacity = '0.6';
            lbl.style.display = 'block';

            const inp = document.createElement('input');
            inp.type = 'number';
            if (paramDef.ui?.min != null) inp.min = paramDef.ui.min;
            if (paramDef.ui?.max != null) inp.max = paramDef.ui.max;
            inp.step = paramDef.ui?.step ?? 0.01;
            inp.value = defaults[i] ?? 0;
            inp.style.width = '100%';
            inputs.push(inp);

            wrap.appendChild(lbl);
            wrap.appendChild(inp);
            container.appendChild(wrap);
        }

        inputs.forEach(inp => {
            inp.addEventListener('change', () => {
                const values = inputs.map(i => parseFloat(i.value));
                this._emitChange(paramPath, values, components);
            });
        });

        return container;
    }

    /** @private — push button */
    _createButton(paramPath, paramDef) {
        const btn = document.createElement('button');
        btn.className = 'btn';
        btn.textContent = paramDef.ui?.label || paramDef.name || 'Execute';
        btn.style.marginTop = '4px';

        btn.addEventListener('click', () => {
            this._emitChange(paramPath, 1, 1);
        });

        return btn;
    }

    /** @private — checkbox / toggle */
    _createCheckbox(paramPath, paramDef) {
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.checked = paramDef.default ?? false;

        input.addEventListener('change', () => {
            this._emitChange(paramPath, input.checked, 1);
        });

        return input;
    }

    /** @private — dropdown select */
    _createMenu(paramPath, paramDef) {
        const select = document.createElement('select');

        if (paramDef.ui?.options) {
            paramDef.ui.options.forEach(opt => {
                const option = document.createElement('option');
                option.value = opt.value;
                option.textContent = opt.label;
                if (opt.value === paramDef.default) option.selected = true;
                select.appendChild(option);
            });
        }

        select.addEventListener('change', () => {
            const val = parseInt(select.value);
            this._emitChange(paramPath, isNaN(val) ? select.value : val, 1);
        });

        return select;
    }

    /** @private — text input or multiline textarea */
    _createString(paramPath, paramDef) {
        if (paramDef.ui?.multiline) {
            const textarea = document.createElement('textarea');
            textarea.value = paramDef.default ?? '';
            textarea.rows = 3;

            textarea.addEventListener('change', () => {
                this._emitChange(paramPath, textarea.value, 1);
            });

            return textarea;
        }

        const input = document.createElement('input');
        input.type = 'text';
        input.value = paramDef.default ?? '';

        input.addEventListener('change', () => {
            this._emitChange(paramPath, input.value, 1);
        });

        return input;
    }

    /** @private — colour picker (Houdini RGB 0-1 ↔ hex) */
    _createColor(paramPath, paramDef) {
        const input = document.createElement('input');
        input.type = 'color';

        const rgb = paramDef.default || [1, 1, 1];
        input.value = '#' + rgb.map(x => {
            const h = Math.round(x * 255).toString(16);
            return h.length === 1 ? '0' + h : h;
        }).join('');

        input.addEventListener('change', () => {
            const hex = input.value;
            const rgbOut = [
                parseInt(hex.slice(1, 3), 16) / 255,
                parseInt(hex.slice(3, 5), 16) / 255,
                parseInt(hex.slice(5, 7), 16) / 255
            ];
            this._emitChange(paramPath, rgbOut, paramDef.num_components || 3);
        });

        return input;
    }

    /** @private — file browser */
    _createFile(paramPath, paramDef) {
        const container = document.createElement('div');

        const input = document.createElement('input');
        input.type = 'file';

        input.addEventListener('change', () => {
            if (input.files.length > 0) {
                this._emitChange(paramPath, input.files[0].name, 1);
            }
        });

        container.appendChild(input);
        return container;
    }
}
