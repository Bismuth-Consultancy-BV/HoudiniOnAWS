/**
 * Aurora EventEmitter — lightweight event emitter base class.
 *
 * Provides `on`, `off`, and `_emit` for any class that extends it.
 * All Aurora SDK classes that emit events inherit from this base.
 *
 * Usage:
 *   class MyClass extends EventEmitter {
 *       doSomething() {
 *           this._emit('done', { result: 42 });
 *       }
 *   }
 *
 *   const obj = new MyClass();
 *   obj.on('done', data => console.log(data.result));
 */
export class EventEmitter {
    constructor() {
        /** @private */
        this._listeners = {};
    }

    /**
     * Subscribe to an event.
     * @param {string}   event - Event name.
     * @param {Function} fn    - Callback invoked with the event payload.
     * @returns {this} For chaining.
     */
    on(event, fn) {
        (this._listeners[event] ??= []).push(fn);
        return this;
    }

    /**
     * Unsubscribe from an event.
     * @param {string}   event - Event name.
     * @param {Function} fn    - The exact function reference passed to `on`.
     * @returns {this} For chaining.
     */
    off(event, fn) {
        const list = this._listeners[event];
        if (list) this._listeners[event] = list.filter(f => f !== fn);
        return this;
    }

    /**
     * Emit an event to all registered listeners.
     * @param {string} event  - Event name.
     * @param {*}      detail - Payload passed to each listener.
     * @protected
     */
    _emit(event, detail) {
        const list = this._listeners[event];
        if (list) list.forEach(fn => fn(detail));
    }

    /**
     * Remove all listeners. Call during teardown / dispose.
     */
    removeAllListeners() {
        this._listeners = {};
    }
}
