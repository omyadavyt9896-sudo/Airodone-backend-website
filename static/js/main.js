document.addEventListener("DOMContentLoaded", function () {
  const navToggle = document.getElementById("navToggle");
  const mainNav = document.getElementById("mainNav");
  const navBackdrop = document.getElementById("navBackdrop");

  const closeMobileNav = () => {
    if (navToggle && mainNav) {
      navToggle.classList.remove("open");
      navToggle.setAttribute("aria-expanded", "false");
      mainNav.classList.remove("open");
      document.body.classList.remove("nav-open");
    }
  };

  const openMobileNav = () => {
    if (navToggle && mainNav) {
      navToggle.classList.add("open");
      navToggle.setAttribute("aria-expanded", "true");
      mainNav.classList.add("open");
      document.body.classList.add("nav-open");
    }
  };

  if (navToggle && mainNav) {
    navToggle.addEventListener("click", () => {
      const isOpen = navToggle.classList.contains("open");
      if (isOpen) {
        closeMobileNav();
      } else {
        openMobileNav();
      }
    });

    // Close on navigation link click
    mainNav.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", () => {
        closeMobileNav();
      });
    });

    // Close on backdrop click
    if (navBackdrop) {
      navBackdrop.addEventListener("click", closeMobileNav);
    }

    // Close on Escape key
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && navToggle.classList.contains("open")) {
        closeMobileNav();
        navToggle.focus();
      }
    });

    // Close on window resize to desktop
    window.addEventListener("resize", () => {
      if (window.innerWidth >= 992 && navToggle.classList.contains("open")) {
        closeMobileNav();
      }
    });
  }

  // Basic fade-in animation for sections
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
      threshold: 0.16,
    }
  );

  document.querySelectorAll(".section, .page-hero").forEach((el) => {
    el.classList.add("pre-animate");
    observer.observe(el);
  });

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



