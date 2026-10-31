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

  /* ------------------------------------------------- guilloche --- */
  /* Engraved signature pattern of the Draft E skin: a hypotrochoid rosette
     turned into an SVG path (fill none, hairline stroke) and used for the
     hero halo, the horizontal band, the certificate watermark and the seal. */
  var SVG_NS = "http://www.w3.org/2000/svg";
  function rosette(n, R, d, turns) {
    var r = R / (n + 1), g = R - r, pts = [], i, t;
    for (i = 0; i <= 1800; i++) {
      t = i / 1800 * Math.PI * 2 * turns;
      pts.push((g * Math.cos(t) + d * Math.cos(n * t)).toFixed(2) + "," +
        (g * Math.sin(t) - d * Math.sin(n * t)).toFixed(2));
    }
    return "M" + pts.join("L");
  }
  function addPath(parent, d, animate) {
    var p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", ".7");
    p.setAttribute("vector-effect", "non-scaling-stroke");
    parent.appendChild(p);
    if (animate && !reduced) {
      var L = p.getTotalLength();
      p.style.strokeDasharray = String(L);
      p.style.strokeDashoffset = String(L);
      requestAnimationFrame(function () {
        p.style.transition = "stroke-dashoffset 2.4s cubic-bezier(.2,.7,.2,1)";
        p.style.strokeDashoffset = "0";
      });
    }
    return p;
  }
  function svgEl(name, attrs) {
    var el = document.createElementNS(SVG_NS, name), k;
    for (k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) el.setAttribute(k, attrs[k]);
    }
    return el;
  }

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
    loadDict(l).then(function (d) { applyDict(d); renderShelf(); });
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

  /* ------------------------------------------ skills shelf (round C, S1) ---
     The home shelf mounts cards from skills/catalog.json — the SAME file
     /skills reads, so the two pages can no longer contradict each other.
     The grid stays [hidden] until at least one card exists: no-JS readers
     see the caption and the two doors, and a JS fetch that fails shows the
     honest skills.empty* state instead of an empty frame. Card text comes
     from the active pack (skills.card.<slug> / skills.cat.<category>) so a
     language switch repaints it like every other string; the catalog's own
     description is the fallback when a pack key does not exist. */
  var shelfGrid = document.getElementById("shelfGrid");
  var shelfEmpty = document.getElementById("shelfEmpty");
  var shelfEntries = null;

  function shelfCard(entry) {
    var d = dicts[lang] || {};
    var wrap = document.createElement("div");
    wrap.className = "shelf-card";
    var cat = document.createElement("span");
    cat.className = "shelf-cat";
    cat.textContent = (entry.category && d["skills.cat." + entry.category]) ||
      entry.category || "";
    var name = document.createElement("h4");
    name.className = "shelf-name";
    name.textContent = entry.name || entry.slug || "";
    var body = document.createElement("p");
    var descKey = "skills.card." + (entry.slug || "");
    body.textContent = d[descKey] || entry.description || "";
    wrap.appendChild(cat);
    wrap.appendChild(name);
    wrap.appendChild(body);
    return wrap;
  }

  function renderShelf() {
    if (!shelfGrid || !shelfEntries || !shelfEntries.length) return;
    shelfGrid.innerHTML = "";
    shelfEntries.slice(0, 6).forEach(function (entry) {
      shelfGrid.appendChild(shelfCard(entry));
    });
    shelfGrid.hidden = false;
    if (shelfEmpty) shelfEmpty.hidden = true;
  }

  function shelfFallback() {
    if (shelfGrid) shelfGrid.hidden = true;
    if (shelfEmpty) shelfEmpty.hidden = false;
  }

  if (shelfGrid) {
    fetch("skills/catalog.json")
      .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
      .then(function (d) {
        shelfEntries = (d && Array.isArray(d.skills)) ? d.skills : [];
        if (!shelfEntries.length) { shelfFallback(); return; }
        renderShelf();
      })
      .catch(shelfFallback);
  }

  /* --------------------------------------------------- fact ticker --- */
  var track = document.getElementById("factTrack");
  if (track) track.innerHTML += track.innerHTML; // seamless -50% loop

  /* ------------------------------------- engraved graphics (Draft E) --- */
  // hero halo: three concentric rosettes that draw themselves in
  (function () {
    var halo = document.getElementById("halo");
    if (!halo) return;
    [[17, 150, 42, 1], [13, 124, 34, 1], [9, 98, 26, 1]].forEach(function (a, i) {
      var p = addPath(halo, rosette(a[0], a[1], a[2], a[3]), true);
      p.style.transitionDelay = (i * 0.28) + "s";
    });
  })();

  // horizontal banknote band under the hero
  (function () {
    var band = document.getElementById("band1");
    if (!band) return;
    var out = "", k, x, pts, y;
    for (k = 0; k < 7; k++) {
      pts = [];
      for (x = 0; x <= 1440; x += 8) {
        y = 17 + Math.sin(x / 58 + k * Math.PI / 7) * (7.4 - k * 0.55) *
          Math.sin(x / 1440 * Math.PI);
        pts.push(x.toFixed(0) + "," + y.toFixed(2));
      }
      out += '<path d="M' + pts.join("L") + '" fill="none" stroke="currentColor"' +
        ' stroke-width=".6" opacity="' + (0.85 - k * 0.08).toFixed(2) + '"/>';
    }
    band.innerHTML = out;
  })();

  // the watch: one continuous engraved rail, lit by scroll (nodes are
  // visible by default — scrolling only lights the marks up).
  // Geometry comes from the live layout instead of a fixed viewBox: the rail
  // runs through the node centres and every mark is stitched to the line by a
  // short horizontal rule, so the connection reads at any viewport width.
  (function () {
    var rail = document.getElementById("rail");
    var spine = document.getElementById("spine");
    if (!rail || !spine) return;
    var beats = Array.prototype.slice.call(spine.querySelectorAll(".beat"));
    var drawPath = null, len = 0, prog = 0, conns = [];

    function geom() {
      var box = spine.getBoundingClientRect();
      var W = Math.round(box.width), H = Math.round(box.height);
      if (!W || !H) return false;
      rail.setAttribute("viewBox", "0 0 " + W + " " + H);
      while (rail.firstChild) rail.removeChild(rail.firstChild);
      conns = [];
      var pts = [];
      beats.forEach(function (b) {
        var n = b.querySelector(".node"), c = b.querySelector(".beat-card");
        if (!n || !c) return;
        var nb = n.getBoundingClientRect(), cb = c.getBoundingClientRect();
        pts.push({ x: nb.left - box.left + nb.width / 2,
          y: nb.top - box.top + nb.height / 2,
          l: cb.left - box.left, r: cb.right - box.left, beat: b });
      });
      if (!pts.length) return false;

      // card edge -> node centre: the stitch that ties each mark to the line
      pts.forEach(function (pt) {
        var ln = null;
        if (pt.r <= pt.x + 1) {
          ln = svgEl("line", { x1: pt.r, y1: pt.y, x2: pt.x, y2: pt.y });
        } else if (pt.l >= pt.x - 1) {
          ln = svgEl("line", { x1: pt.x, y1: pt.y, x2: pt.l, y2: pt.y });
        }
        if (ln) {
          ln.setAttribute("stroke", "#a5b7ca");
          ln.setAttribute("stroke-width", "1");
          rail.appendChild(ln);
          conns.push({ el: ln, beat: pt.beat });
        }
      });

      // one path through every node, bulging alternately like an engraved line
      var d = "M" + pts[0].x.toFixed(1) + " 0 L" + pts[0].x.toFixed(1) + " " +
        pts[0].y.toFixed(1), side = 1, i, p, q, dy, room, s, amp;
      for (i = 1; i < pts.length; i++) {
        p = pts[i - 1]; q = pts[i]; dy = q.y - p.y;
        s = side;
        room = s > 0 ? (W - p.x - 8) : (p.x - 8);
        if (room < 12) {
          s = -s;
          room = s > 0 ? (W - p.x - 8) : (p.x - 8);
        }
        amp = Math.min(34, Math.max(8, room));
        d += " C" + (p.x + s * amp).toFixed(1) + " " +
          (p.y + dy * 0.34).toFixed(1) + " " + (q.x - s * amp).toFixed(1) + " " +
          (p.y + dy * 0.66).toFixed(1) + " " + q.x.toFixed(1) + " " +
          q.y.toFixed(1);
        side = -side;
      }
      d += " L" + pts[pts.length - 1].x.toFixed(1) + " " + H;

      rail.appendChild(svgEl("path", { d: d, fill: "none", stroke: "#a5b7ca",
        "stroke-width": "1.2" }));
      drawPath = svgEl("path", { d: d, fill: "none", stroke: "#2892ff",
        "stroke-width": "1.6", "stroke-linecap": "round" });
      rail.appendChild(drawPath);
      len = drawPath.getTotalLength();
      drawPath.style.strokeDasharray = String(len);
      drawPath.style.strokeDashoffset = reduced ? "0" : String(len * (1 - prog));
      return true;
    }

    function light() {
      var box = spine.getBoundingClientRect();
      var span = box.height * 0.82 || 1;
      prog = Math.min(1, Math.max(0,
        (window.innerHeight * 0.72 - box.top) / span));
      if (drawPath && !reduced) {
        drawPath.style.strokeDashoffset = String(len * (1 - prog));
      }
      beats.forEach(function (b) {
        b.classList.toggle("on", b.getBoundingClientRect().top <
          window.innerHeight * 0.68);
      });
      conns.forEach(function (c) {
        c.el.setAttribute("stroke", c.beat.classList.contains("on") ?
          "#2892ff" : "#a5b7ca");
      });
    }

    function rebuild() { if (geom()) light(); }
    var rt;
    window.addEventListener("scroll", light, { passive: true });
    window.addEventListener("resize", function () {
      clearTimeout(rt); rt = setTimeout(rebuild, 150);
    });
    window.addEventListener("load", rebuild);
    rebuild();
  })();

  // certificate watermark + free seal
  (function () {
    var wm = document.getElementById("certWm");
    if (wm) {
      addPath(wm, rosette(19, 140, 40, 1), false);
      addPath(wm, rosette(11, 104, 30, 1), false);
    }
    var seal = document.getElementById("certSeal");
    if (!seal) return;
    seal.appendChild(svgEl("circle", { r: "140", fill: "none",
      stroke: "currentColor", "stroke-width": "1" }));
    addPath(seal, rosette(15, 126, 36, 1), true);
    addPath(seal, rosette(9, 84, 24, 1), true);
    seal.appendChild(svgEl("circle", { r: "46", fill: "none",
      stroke: "currentColor", "stroke-width": "1" }));
    var txt = svgEl("text", { "text-anchor": "middle", y: "7", "font-size": "30",
      fill: "currentColor", "font-family": "Georgia, serif" });
    txt.textContent = "free";
    seal.appendChild(txt);
  })();

  // the portrait is present, not printed: the plate follows the cursor
  (function () {
    var wrapEl = document.querySelector(".plate-wrap");
    var plateEl = document.getElementById("plate");
    if (!wrapEl || !plateEl || reduced) return;
    if (!window.matchMedia || !window.matchMedia("(pointer: fine)").matches) return;
    wrapEl.addEventListener("pointermove", function (e) {
      var r = wrapEl.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width - 0.5;
      var y = (e.clientY - r.top) / r.height - 0.5;
      plateEl.style.transform = "rotateY(" + (x * 7).toFixed(2) +
        "deg) rotateX(" + (-y * 7).toFixed(2) + "deg)";
    });
    wrapEl.addEventListener("pointerleave", function () {
      plateEl.style.transform = "";
    });
  })();

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
    /* engraved wave field: the paper's guilloche under-layer, painted on
       every frame so the canvas always carries ink (gate: non-transparent) */
    function engraveField() {
      ctx.lineWidth = 1;
      for (var k = 0; k < 7; k++) {
        ctx.beginPath();
        for (var ex = 0; ex <= w; ex += 12) {
          var ey = h * (0.12 + k * 0.13) + Math.sin(ex / 90 + k * 1.7) * 14 *
            Math.sin((ex / (w || 1)) * Math.PI);
          if (ex === 0) ctx.moveTo(ex, ey); else ctx.lineTo(ex, ey);
        }
        ctx.strokeStyle = "rgba(0,22,51," + (0.05 + k * 0.011).toFixed(3) + ")";
        ctx.stroke();
      }
    }
    function draw(t) {
      ctx.clearRect(0, 0, w, h);
      engraveField();
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

  /* --------------------------------------------- copy buttons ----- */
  /* Copy the command out of its dark inset block. navigator.clipboard needs
     a secure context; when it is missing or refuses (plain http, denied
     permission), fall back to a hidden textarea + execCommand — and if that
     fails too, fail silently: a copy button must never throw. Success copy
     comes from the locale pack (start.copied), language-aware. */
  function dictVal(key) {
    var d = dicts[lang];
    if (d && Object.prototype.hasOwnProperty.call(d, key)) return d[key];
    return null;
  }
  function legacyCopy(text) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return !!ok;
    } catch (e) { return false; }
  }
  function flashCopied(btn) {
    btn.textContent = dictVal("start.copied") || "Copied";
    btn.classList.add("copied");
    clearTimeout(btn._copyTimer);
    btn._copyTimer = setTimeout(function () {
      btn.textContent = dictVal(btn.getAttribute("data-i18n")) || "Copy";
      btn.classList.remove("copied");
    }, 1800);
  }
  document.querySelectorAll(".copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var host = btn.closest(".cmd");
      var code = host ? host.querySelector("code") : null;
      var text = code ? code.textContent : "";
      if (!text) return;
      var done = function (ok) { if (ok) flashCopied(btn); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () { done(true); },
          function () { done(legacyCopy(text)); }
        );
      } else {
        done(legacyCopy(text));
      }
    });
  });

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
  /* --------------------------------------------- visits counter --- */
  /* Footer page-view counter: GET /api/visits once per page load. Every
     load counts (refreshes and crawlers included — approximate by design).
     The endpoint only exists on the deployed site; the local static preview
     server has no /api/visits route, so loopback hosts skip the probe (a 404
     there would only pollute the console). If the endpoint is missing, fails,
     or returns a non-numeric count, the whole block stays hidden: never
     render a fabricated number. */
  var vw = document.getElementById("visitsWrap");
  var vc = document.getElementById("visits");
  function loopbackHost() {
    var h = location.hostname;
    return !h || h === "127.0.0.1" || h === "localhost" ||
      h === "[::1]" || h === "::1";
  }
  if (vw && vc && !loopbackHost()) {
    fetch("/api/visits", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("http " + r.status); return r.json(); })
      .then(function (d) {
        if (!d || typeof d.count !== "number") throw new Error("bad shape");
        vc.textContent = String(d.count);
        vw.hidden = false;
      })
      .catch(function () { vw.hidden = true; });
  }

  var yr = document.getElementById("year");
  if (yr) yr.textContent = String(new Date().getFullYear());
})();
