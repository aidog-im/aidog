/* I'm AiDog site — minimal vanilla JS: i18n loader, nav, ambience.
   No dependencies, no CDN. All copy lives in ../i18n/*.json (English defaults
   are also inline in index.html so the page reads with JS/fetch disabled). */
(function () {
  "use strict";

  var LANG_KEY = "aidog.site.lang";
  var qsLang = null;        // ?lang=en|zh — one-off shareable override
  try {
    var m = location.search.match(/[?&]lang=(en|zh)\b/);
    if (m) qsLang = m[1];
  } catch (e) { /* ignore */ }
  var lang = qsLang;
  if (!lang) {
    try { lang = localStorage.getItem(LANG_KEY); } catch (e) { /* private mode */ }
  }
  if (lang !== "en" && lang !== "zh") lang = "en";

  var dicts = {};           // cache: lang -> {key: value}
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------- i18n --- */
  function loadDict(l) {
    if (dicts[l]) return Promise.resolve(dicts[l]);
    return fetch("i18n/" + l + ".json")
      .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
      .then(function (d) { dicts[l] = d; return d; })
      .catch(function () { return null; }); // file:// without a server: keep inline EN
  }

  function applyDict(d) {
    if (!d) return;
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      var k = el.getAttribute("data-i18n");
      if (Object.prototype.hasOwnProperty.call(d, k)) el.textContent = d[k];
    });
    document.querySelectorAll("[data-i18n-alt]").forEach(function (el) {
      var k = el.getAttribute("data-i18n-alt");
      if (Object.prototype.hasOwnProperty.call(d, k)) el.setAttribute("alt", d[k]);
    });
  }

  function setLang(l) {
    lang = l;
    try { localStorage.setItem(LANG_KEY, l); } catch (e) { /* ignore */ }
    document.documentElement.lang = (l === "zh") ? "zh-Hans" : "en";
    var label = document.getElementById("langLabel");
    if (label) label.textContent = (l === "en") ? "ZH" : "EN";  // pack value overwrites via data-i18n
    loadDict(l).then(applyDict);
  }

  var langBtn = document.getElementById("langBtn");
  if (langBtn) langBtn.addEventListener("click", function () {
    setLang(lang === "en" ? "zh" : "en");
    // keep the URL shareable: sync ?lang= on an explicit toggle only
    // (a plain visit must never rewrite the address bar)
    try {
      var url = new URL(location.href);
      url.searchParams.set("lang", lang);
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    } catch (e) { /* file:// or ancient browser: state only */ }
  });
  setLang(lang); // en by default; applies JSON over the inline English defaults

  /* ------------------------------------------------------------ nav --- */
  var burger = document.getElementById("burger");
  var navLinks = document.getElementById("navLinks");
  if (burger && navLinks) {
    burger.addEventListener("click", function () {
      var open = navLinks.classList.toggle("open");
      burger.setAttribute("aria-expanded", String(open));
    });
    navLinks.addEventListener("click", function (e) {
      if (e.target.classList.contains("nav-link")) navLinks.classList.remove("open");
    });
  }

  var links = Array.prototype.slice.call(document.querySelectorAll(".nav-link"));
  var sections = links
    .map(function (a) { return document.querySelector(a.getAttribute("href")); })
    .filter(Boolean);

  function spy() {
    var y = window.scrollY + window.innerHeight * 0.32;
    var current = sections[0];
    sections.forEach(function (s) { if (s.offsetTop <= y) current = s; });
    links.forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("href") === "#" + current.id);
    });
  }
  window.addEventListener("scroll", spy, { passive: true });
  spy();

  /* --------------------------------------------------------- reveal --- */
  var reveals = document.querySelectorAll(".reveal");
  if (reduced || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12 });
    reveals.forEach(function (el) { io.observe(el); });
  }

  /* --------------------------------------------------- fact ticker --- */
  var track = document.getElementById("factTrack");
  if (track) track.innerHTML += track.innerHTML; // seamless -50% loop

  /* --------------------------------------------------- droplet field -- */
  /* Falling teardrops over the hero: soft blue/cyan drops drifting upward
     like bubbles in water — the logo's water-drop motif, canvas edition. */
  var cv = document.getElementById("drops");
  if (cv && cv.getContext) {
    var ctx = cv.getContext("2d"), drops = [], raf = 0, w = 0, h = 0;
    function size() {
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = cv.clientWidth; h = cv.clientHeight;
      cv.width = w * dpr; cv.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var n = Math.min(70, Math.round(w * h / 26000));
      drops = [];
      for (var i = 0; i < n; i++) {
        drops.push({
          x: Math.random() * w, y: Math.random() * h,
          r: Math.random() * 7 + 3,
          a: Math.random() * 0.28 + 0.14,
          s: Math.random() * 0.22 + 0.05,
          p: Math.random() * Math.PI * 2,
          c: Math.random() < 0.45 ? "40,146,255" :
             (Math.random() < 0.75 ? "112,228,253" : "147,233,249")
        });
      }
    }
    /* teardrop path: pointed tip on top, round body below */
    function dropPath(x, y, r) {
      ctx.beginPath();
      ctx.moveTo(x, y - r * 1.7);
      ctx.bezierCurveTo(x + r * 0.95, y - r * 0.7,
        x + r, y - r * 0.1, x + r, y + r * 0.1);
      ctx.arc(x, y + r * 0.1, r, 0, Math.PI, false);
      ctx.bezierCurveTo(x - r, y - r * 0.1, x - r * 0.95, y - r * 0.7,
        x, y - r * 1.7);
      ctx.closePath();
    }
    function draw(t) {
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < drops.length; i++) {
        var d = drops[i];
        var sway = reduced ? 0 : Math.sin(t / 1400 + d.p) * 6;
        var tw = reduced ? d.a : d.a * (0.7 + 0.3 * Math.sin(t / 1000 + d.p));
        dropPath(d.x + sway, d.y, d.r);
        ctx.fillStyle = "rgba(" + d.c + "," + tw.toFixed(3) + ")";
        ctx.fill();
        /* specular highlight: the wet look */
        ctx.beginPath();
        ctx.arc(d.x + sway - d.r * 0.3, d.y + d.r * 0.25,
          Math.max(1, d.r * 0.22), 0, 6.2832);
        ctx.fillStyle = "rgba(255,255,255," + (tw * 1.6).toFixed(3) + ")";
        ctx.fill();
        if (!reduced) {
          d.y -= d.s; if (d.y < -20) { d.y = h + 20; d.x = Math.random() * w; }
        }
      }
      if (!reduced) raf = requestAnimationFrame(draw);
    }
    size();
    draw(0);
    var rt;
    window.addEventListener("resize", function () {
      clearTimeout(rt);
      rt = setTimeout(function () { size(); if (reduced) draw(0); }, 150);
    });
    document.addEventListener("visibilitychange", function () {
      if (reduced) return;
      if (document.hidden) { cancelAnimationFrame(raf); }
      else { raf = requestAnimationFrame(draw); }
    });
  }

  /* --------------------------------------------- mock ring ticks ---- */
  var ticks = document.getElementById("ringTicks");
  if (ticks) {
    var ns = "http://www.w3.org/2000/svg", frag = document.createDocumentFragment();
    for (var a = 0; a < 360; a += 6) {
      var rad = (a - 90) * Math.PI / 180;
      var r1 = 176, r2 = (a % 30 === 0) ? 164 : 171;
      var ln = document.createElementNS(ns, "line");
      ln.setAttribute("x1", (210 + Math.cos(rad) * r1).toFixed(1));
      ln.setAttribute("y1", (210 + Math.sin(rad) * r1).toFixed(1));
      ln.setAttribute("x2", (210 + Math.cos(rad) * r2).toFixed(1));
      ln.setAttribute("y2", (210 + Math.sin(rad) * r2).toFixed(1));
      ln.setAttribute("stroke", (a % 30 === 0) ? "rgba(40,146,255,.75)" : "rgba(165,183,202,.6)");
      ln.setAttribute("stroke-width", "1");
      frag.appendChild(ln);
    }
    ticks.appendChild(frag);
  }

  /* ------------------------------------------- mock clock + year ---- */
  var mockClock = document.getElementById("mockClock");
  function two(n) { return n < 10 ? "0" + n : "" + n; }
  function tick() {
    if (mockClock) {
      var d = new Date();
      mockClock.textContent = two(d.getHours()) + ":" + two(d.getMinutes()) + ":" + two(d.getSeconds());
    }
  }
  tick(); setInterval(tick, 1000);
  var yr = document.getElementById("year");
  if (yr) yr.textContent = String(new Date().getFullYear());
})();
