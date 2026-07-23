/** Reliable activation bridge for the read-only UMIL workspace. */
(() => {
    'use strict';

    let lastDispatchAt = 0;

    function intelligenceIsSelected() {
        const hash = String(window.location.hash || '').replace(/^#/, '').toLowerCase();
        if (hash === 'intelligence') return true;

        const selectedTarget = document.querySelector(
            '[data-workspace-target="intelligence"].active,' +
            '[data-workspace-target="intelligence"][aria-selected="true"]'
        );
        if (selectedTarget) return true;

        const workspace = document.querySelector('[data-workspace="intelligence"]');
        if (!workspace || workspace.hidden || workspace.getAttribute('aria-hidden') === 'true') {
            return false;
        }

        return window.getComputedStyle(workspace).display !== 'none';
    }

    function dispatchActivation() {
        if (!intelligenceIsSelected()) return;

        const now = Date.now();
        if (now - lastDispatchAt < 100) return;
        lastDispatchAt = now;

        window.dispatchEvent(new CustomEvent('ats:workspace', {
            detail: { name: 'intelligence', source: 'umil-bootstrap' },
        }));
    }

    function scheduleActivation() {
        window.requestAnimationFrame(() => window.setTimeout(dispatchActivation, 0));
    }

    window.addEventListener('load', scheduleActivation, { once: true });
    window.addEventListener('hashchange', scheduleActivation);
    window.addEventListener('pageshow', scheduleActivation);

    document.addEventListener('click', event => {
        if (event.target.closest('[data-workspace-target="intelligence"]')) {
            window.setTimeout(dispatchActivation, 0);
        }
    });

    const observerTarget = document.querySelector('#app') || document.body;
    if (observerTarget) {
        const observer = new MutationObserver(scheduleActivation);
        observer.observe(observerTarget, {
            subtree: true,
            attributes: true,
            attributeFilter: ['class', 'hidden', 'aria-selected', 'aria-hidden'],
        });
    }

    window.setTimeout(dispatchActivation, 100);
    window.setTimeout(dispatchActivation, 750);
})();
