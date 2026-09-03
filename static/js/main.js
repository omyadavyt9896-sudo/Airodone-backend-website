document.addEventListener("DOMContentLoaded", function () {
  const navToggle = document.getElementById("navToggle");
  const mainNav = document.getElementById("mainNav");

  if (navToggle && mainNav) {
    navToggle.addEventListener("click", () => {
      const isOpen = navToggle.classList.toggle("open");
      if (isOpen) {
        mainNav.classList.add("open");
      } else {
        mainNav.classList.remove("open");
      }
    });
  }

  // Accessible Scroll Reveal Observer
  const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (prefersReducedMotion) {
    document.querySelectorAll(".section, .hero, .page-hero, .category-card, .course-card-v2").forEach((el) => {
      el.classList.add("in-view");
    });
  } else if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in-view");
            observer.unobserve(entry.target);
          }
        });
      },
      {
        threshold: 0.12,
        rootMargin: "0px 0px -40px 0px",
      }
    );

    document.querySelectorAll(".section, .hero, .page-hero, .category-grid, .card-grid").forEach((el) => {
      el.classList.add("pre-animate");
      observer.observe(el);
    });
  }

  // Quiz Start Fullscreen User Gesture Listener (Phase 7.6A)
  document.querySelectorAll(".js-start-quiz-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().catch(function (err) {
          console.log("Fullscreen request deferred or blocked by browser:", err);
        });
      }
    });
  });
});

/* ==========================================================================
   Global Modal System Helper Functions (Phase 7.6A)
   ========================================================================== */
let lastFocusedElement = null;

function openComingSoonModal(videoTitle) {
  lastFocusedElement = document.activeElement;
  const modal = document.getElementById("comingSoonModal");
  const videoNameEl = document.getElementById("comingSoonVideoTitle");
  const primaryBtn = document.getElementById("comingSoonPrimaryBtn");

  if (!modal) return;

  if (videoTitle) {
    if (videoNameEl) {
      videoNameEl.textContent = '"' + videoTitle + '"';
      videoNameEl.style.display = "block";
    }
  } else {
    if (videoNameEl) {
      videoNameEl.style.display = "none";
    }
  }

  modal.removeAttribute("hidden");
  requestAnimationFrame(() => {
    modal.classList.add("active");
  });

  if (primaryBtn) {
    setTimeout(() => primaryBtn.focus(), 50);
  }
}

function closeComingSoonModal() {
  const modal = document.getElementById("comingSoonModal");
  if (!modal) return;

  modal.classList.remove("active");
  setTimeout(() => {
    modal.setAttribute("hidden", "");
    if (lastFocusedElement && typeof lastFocusedElement.focus === "function") {
      lastFocusedElement.focus();
    }
  }, 200);
}

// Global modal keydown & backdrop click listeners
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") {
    const modal = document.getElementById("comingSoonModal");
    if (modal && !modal.hasAttribute("hidden") && modal.classList.contains("active")) {
      closeComingSoonModal();
    }
  }
});

document.addEventListener("click", function (e) {
  const modal = document.getElementById("comingSoonModal");
  if (modal && e.target === modal) {
    closeComingSoonModal();
  }
});



