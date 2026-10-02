/**
 * Airodrone Technical Custom Cursor
 * - Active ONLY on devices with a fine pointer and hover capability
 * - requestAnimationFrame interpolation with lerp
 * - Zero idle CPU overhead (stops loop when stationary)
 * - Accessibility: aria-hidden, pointer-events: none, respects prefers-reduced-motion
 */
(function () {
    // 1. Guard: Check for fine pointer and hover capability
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
        return;
    }

    const dot = document.getElementById('cursorDot');
    const ring = document.getElementById('cursorRing');
    if (!dot || !ring) return;

    let mouseX = -100;
    let mouseY = -100;
    let ringX = -100;
    let ringY = -100;
    let isVisible = false;
    let isMoving = false;
    let isOverInput = false;

    const lerpFactor = 0.22;
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Body class helpers
    const HOVER_CLASSES = [
        'cursor-hover-btn',
        'cursor-hover-link',
        'cursor-hover-card',
        'cursor-hover-media'
    ];

    function clearHoverClasses() {
        HOVER_CLASSES.forEach((cls) => document.body.classList.remove(cls));
    }

    function renderRing() {
        if (prefersReducedMotion) {
            ringX = mouseX;
            ringY = mouseY;
        } else {
            ringX += (mouseX - ringX) * lerpFactor;
            ringY += (mouseY - ringY) * lerpFactor;
        }

        ring.style.transform = `translate3d(${ringX}px, ${ringY}px, 0)`;

        const dx = Math.abs(mouseX - ringX);
        const dy = Math.abs(mouseY - ringY);

        if (dx > 0.15 || dy > 0.15) {
            requestAnimationFrame(renderRing);
        } else {
            isMoving = false;
        }
    }

    // 2. Mouse Movement Tracking
    window.addEventListener('mousemove', (e) => {
        mouseX = e.clientX;
        mouseY = e.clientY;

        dot.style.transform = `translate3d(${mouseX}px, ${mouseY}px, 0)`;

        if (!isVisible) {
            isVisible = true;
            if (!isOverInput) {
                dot.classList.remove('cursor--hidden');
                ring.classList.remove('cursor--hidden');
            }
        }

        if (!isMoving) {
            isMoving = true;
            requestAnimationFrame(renderRing);
        }
    }, { passive: true });

    // 3. Viewport Boundaries
    document.addEventListener('mouseleave', () => {
        dot.classList.add('cursor--hidden');
        ring.classList.add('cursor--hidden');
    });

    document.addEventListener('mouseenter', () => {
        if (isVisible && !isOverInput) {
            dot.classList.remove('cursor--hidden');
            ring.classList.remove('cursor--hidden');
        }
    });

    // 4. Delegated Hover Interactions
    document.addEventListener('mouseover', (e) => {
        const target = e.target;
        if (!target) return;

        // Form Inputs: Return to native browser cursor
        if (target.closest('input, textarea, select, [contenteditable="true"]')) {
            isOverInput = true;
            dot.classList.add('cursor--hidden');
            ring.classList.add('cursor--hidden');
            clearHoverClasses();
            return;
        } else if (isOverInput) {
            isOverInput = false;
            if (isVisible) {
                dot.classList.remove('cursor--hidden');
                ring.classList.remove('cursor--hidden');
            }
        }

        // Buttons & interactive triggers
        if (target.closest('button, .btn, .theme-toggle-btn, .nav-toggle, [role="button"]')) {
            clearHoverClasses();
            document.body.classList.add('cursor-hover-btn');
            return;
        }

        // Links & navigation
        if (target.closest('a, .nav-link')) {
            clearHoverClasses();
            document.body.classList.add('cursor-hover-link');
            return;
        }

        // Product Cards
        if (target.closest('.product-card, .card')) {
            clearHoverClasses();
            document.body.classList.add('cursor-hover-card');
            return;
        }

        // Images & Media
        if (target.closest('.product-img-wrap, .hero-media, img')) {
            clearHoverClasses();
            document.body.classList.add('cursor-hover-media');
            return;
        }

        clearHoverClasses();
    }, { passive: true });
})();
