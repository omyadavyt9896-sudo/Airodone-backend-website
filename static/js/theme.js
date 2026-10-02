// Check and apply theme directly on parsing to prevent flash
const savedTheme = localStorage.getItem("theme");
if (savedTheme === "dark") {
    document.documentElement.classList.add("dark-mode");
    document.body?.classList.add("dark-mode");
}

document.addEventListener("DOMContentLoaded", () => {
    if (savedTheme === "dark") {
        document.body.classList.add("dark-mode");
    }

    const toggles = document.querySelectorAll(".theme-toggle-btn");
    if (!toggles.length) return;

    const sunIcon = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`;
    const moonIcon = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;

    // Helper to update toggle button icons based on dark mode state
    const updateToggleIcon = () => {
        const isDark = document.body.classList.contains("dark-mode");
        toggles.forEach((btn) => {
            btn.innerHTML = isDark ? sunIcon : moonIcon;
            btn.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
        });
    };

    // Initialize icons on page load
    updateToggleIcon();

    toggles.forEach((btn) => {
        btn.addEventListener("click", () => {
            const willBeDark = !document.body.classList.contains("dark-mode");
            document.body.classList.toggle("dark-mode", willBeDark);
            document.documentElement.classList.toggle("dark-mode", willBeDark);
            localStorage.setItem("theme", willBeDark ? "dark" : "light");
            updateToggleIcon();
        });
    });
});
