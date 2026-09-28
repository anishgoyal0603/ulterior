/**
 * Landing page behaviour. Three small jobs, all optional: the page is fully
 * readable and every link works with this file absent.
 *
 *   1. the mobile menu button;
 *   2. a hairline under the sticky nav once the page has scrolled;
 *   3. sections easing in as they reach the viewport.
 *
 * Nothing here touches audit data, so there is no escaping to do -- but it
 * also never writes an inline style attribute or cssText: the appearance is
 * owned by landing.css, which is what keeps this page rendering under a
 * strict `style-src 'self'` (see tests/test_frontend_csp_safety.py).
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var reduceMotion = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------- 1. mobile menu */
  var toggle = document.getElementById("nav-toggle");
  var menu = document.getElementById("nav-menu");

  function setMenu(open) {
    if (!toggle || !menu) return;
    menu.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  if (toggle && menu) {
    toggle.addEventListener("click", function () {
      setMenu(toggle.getAttribute("aria-expanded") !== "true");
    });
    // Choosing a destination closes the menu, including in-page anchors,
    // which would otherwise leave it covering the section just jumped to.
    menu.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setMenu(false);
    });
  }

  /* ------------------------------------------------------ 2. nav hairline */
  var nav = document.querySelector(".nav");
  function onScroll() {
    if (nav) nav.classList.toggle("is-scrolled", window.scrollY > 8);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ------------------------------------------------------ 3. scroll reveal */
  // Skipped entirely when the reader asked for less motion, or when the
  // browser cannot tell us what is on screen -- in both cases the content is
  // simply shown, never left hidden waiting for an event that will not come.
  if (reduceMotion || !("IntersectionObserver" in window)) return;

  var targets = document.querySelectorAll(
    ".section-head, .pcard, .tier-key, .step, .split-copy, .sample, " +
    ".wcard, .stat, .popup-fig, .final-in"
  );
  if (!targets.length) return;

  root.classList.add("js");
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        io.unobserve(entry.target);
      }
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });

  targets.forEach(function (el) {
    el.classList.add("reveal");
    io.observe(el);
  });
})();
