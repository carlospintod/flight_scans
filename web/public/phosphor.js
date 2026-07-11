/* vendored from 99tools/design-system/phosphor.js (Phosphor v4.4, 2026-07-12)
   for the flight_scans landing. Token names are bridged in globals.css
   (:root maps --build/--measure/--text-mid/--mono/--bg to the @theme vars).
   Do not edit here; re-vendor from the umbrella when it moves. */
/* ═══════════════════════════════════════════════════════════════════
   99 · Phosphor v4.3 behaviors — theme, reveals, the signals ticker,
   pointer light (fx tilt/specular), magnetic CTAs, the SVG ruler, and
   THE MEASURING FIELD: a full-bleed constellation that links to the
   pointer with hairline survey lines and reads out the distance.

   Color rule: JS never DEFINES a color — the field reads --build /
   --measure / --text-mid from the live tokens (re-read on theme change).
   Reduced motion / no-hover devices get a calm static experience.
   ═══════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";
  document.documentElement.classList.add("js");
  const $ = (s, r) => (r || document).querySelector(s);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hoverable = matchMedia("(hover: hover)").matches;
  /* storage can THROW in cookie-blocked browsers — never let it kill the script */
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* fine */ } },
    sget(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    sset(k, v) { try { sessionStorage.setItem(k, v); } catch (e) { /* fine */ } }
  };

  /* ── theme ── */
  const themeBtn = $("#themeToggle");
  function applyTheme(t) {
    if (t === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
    if (themeBtn) {
      /* the label reports state (instrument voice); the aria explains the action */
      themeBtn.textContent = t === "light" ? "light" : "mocha";
      themeBtn.setAttribute("aria-pressed", t === "light" ? "true" : "false");
      themeBtn.setAttribute("aria-label", "switch to " + (t === "light" ? "mocha" : "light") + " theme");
    }
    /* browser chrome follows the ground color — READ from tokens, never defined here */
    const tc = document.querySelector('meta[name="theme-color"]');
    if (tc) tc.setAttribute("content",
      getComputedStyle(document.documentElement).getPropertyValue("--bg").trim());
  }
  applyTheme(store.get("theme99") === "light" ? "light" : "mocha");
  if (themeBtn) themeBtn.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "light" ? "mocha" : "light";
    store.set("theme99", next);
    applyTheme(next);
    log("theme_change", { to: next });
  });

  /* ── signals ticker ── */
  let logEl = null;
  const t0 = performance.now();
  function mountTicker() {
    if (store.sget("ticker99") === "off" || innerWidth <= 720) return;
    const el = document.createElement("aside");
    el.className = "ticker";
    el.setAttribute("aria-hidden", "true");
    el.innerHTML =
      '<div class="ticker__head"><span class="status-dot status-dot--live"><i></i>signals</span>' +
      '<button type="button" tabindex="-1" title="stop measuring me">×</button></div>' +
      '<div class="ticker__log"></div>';
    document.body.appendChild(el);
    logEl = $(".ticker__log", el);
    $("button", el).addEventListener("click", () => {
      store.sset("ticker99", "off");
      el.remove(); logEl = null;
    });
    log("page_view", { path: location.pathname.split("/").pop() || "/" });
    log("consent", { status: "granted (you kept reading)" });
  }
  function log(name, params) {
    if (!logEl) return;
    const line = document.createElement("div");
    line.className = "new";
    const p = params ? " {" + Object.entries(params).map(([k, v]) => k + ": " + v).join(", ") + "}" : "";
    const t = ((performance.now() - t0) / 1000).toFixed(1);
    line.innerHTML = "<b>▸ " + name + "</b>" + p.replace(/</g, "&lt;") + ' <span style="float:right">' + t + "s</span>";
    logEl.appendChild(line);
    while (logEl.children.length > 4) logEl.removeChild(logEl.firstChild);
  }

  /* ── T2 reveals (never-strand fallback) ── */
  function reveals() {
    const els = document.querySelectorAll(".reveal, .reveal-stagger");
    if (!els.length) return;
    if (reduced || !("IntersectionObserver" in window)) {
      els.forEach(el => el.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver(entries => {
      entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach(el => io.observe(el));
    setTimeout(() => els.forEach(el => el.classList.add("in")), 1400);
  }

  /* ── fx: specular + tilt · magnetic CTAs ── */
  function pointerFX() {
    if (reduced || !hoverable) return;
    document.querySelectorAll(".fx").forEach(el => {
      el.addEventListener("pointermove", e => {
        const r = el.getBoundingClientRect();
        el.style.setProperty("--mx", (e.clientX - r.left) + "px");
        el.style.setProperty("--my", (e.clientY - r.top) + "px");
        const cx = (e.clientX - r.left) / r.width - 0.5;
        const cy = (e.clientY - r.top) / r.height - 0.5;
        el.style.setProperty("--ry", (cx * 3.2).toFixed(2) + "deg");
        el.style.setProperty("--rx", (-cy * 2.6).toFixed(2) + "deg");
      }, { passive: true });
      el.addEventListener("pointerleave", () => {
        el.style.setProperty("--rx", "0deg");
        el.style.setProperty("--ry", "0deg");
      });
    });
    document.querySelectorAll(".btn--magnet").forEach(b => {
      b.addEventListener("pointermove", e => {
        const r = b.getBoundingClientRect();
        const dx = Math.max(-5, Math.min(5, (e.clientX - r.left - r.width / 2) * 0.15));
        const dy = Math.max(-4, Math.min(4, (e.clientY - r.top - r.height / 2) * 0.3));
        b.style.translate = dx.toFixed(1) + "px " + dy.toFixed(1) + "px";
      }, { passive: true });
      b.addEventListener("pointerleave", () => { b.style.translate = ""; });
    });
  }

  /* ── the SVG ruler: built to fit the word it measures ── */
  const NS = "http://www.w3.org/2000/svg";
  function buildRuler(wrap) {
    const word = $(".measure-me", wrap);
    const holder = $(".mruler", wrap);
    if (!word || !holder) return;
    const w = Math.max(48, Math.round(word.getBoundingClientRect().width));
    const fs = parseFloat(getComputedStyle(word).fontSize) || 60;
    const H = Math.max(9, Math.round(fs * 0.17));
    const minor = Math.round(H * 0.45), major = H;
    const withNums = fs >= 56;
    const svg = document.createElementNS(NS, "svg");
    const totalH = H + (withNums ? 12 : 2);
    svg.setAttribute("width", w); svg.setAttribute("height", totalH);
    svg.setAttribute("viewBox", "0 0 " + w + " " + totalH);
    const base = document.createElementNS(NS, "line");
    base.setAttribute("class", "rbase");
    base.setAttribute("x1", 0); base.setAttribute("y1", 0.75);
    base.setAttribute("x2", w); base.setAttribute("y2", 0.75);
    base.style.strokeDasharray = w;
    base.style.strokeDashoffset = w;
    svg.appendChild(base);
    for (let x = 0; x <= w; x += 8) {
      const isMajor = x % 40 === 0;
      const xx = Math.min(x, w - 0.5) + 0.5;
      const t = document.createElementNS(NS, "line");
      t.setAttribute("class", "tick");
      t.setAttribute("x1", xx); t.setAttribute("x2", xx);
      t.setAttribute("y1", 1); t.setAttribute("y2", 1 + (isMajor ? major : minor));
      t.style.setProperty("--d", Math.round(150 + (x / w) * 750) + "ms");
      svg.appendChild(t);
    }
    if (withNums) for (let x = 100; x < w - 26; x += 100) {
      const n = document.createElementNS(NS, "text");
      n.setAttribute("class", "rnum");
      n.setAttribute("x", x + 3.5); n.setAttribute("y", H + 10);
      n.textContent = x;
      n.style.setProperty("--d", Math.round(200 + (x / w) * 750) + "ms");
      svg.appendChild(n);
    }
    holder.textContent = "";
    holder.appendChild(svg);
    return { base, w };
  }

  function rulers() {
    const wraps = [...document.querySelectorAll(".m-wrap")];
    if (!wraps.length) return;
    wraps.forEach(wrap => {
      const word = $(".measure-me", wrap);
      const out = $(".measure-readout", wrap);
      if (!word) return;
      let built = buildRuler(wrap);
      const width = () => Math.round(word.getBoundingClientRect().width);
      let busy = false;
      const count = () => {             /* the readout measures out loud */
        if (!out || busy) return;
        busy = true;
        const w = width();
        /* background-tab guard: rAF may never fire — land the final value */
        const settle = setTimeout(() => { out.textContent = "= " + w + "px · verified"; busy = false; }, 900);
        if (reduced) { clearTimeout(settle); out.textContent = "= " + w + "px · verified"; busy = false; return; }
        const start = performance.now();
        const step = now => {
          if (!busy) return;
          const p = Math.min(1, (now - start) / 500);
          out.textContent = "= " + Math.round(w * p) + "px · " + (p < 1 ? "measuring…" : "verified");
          if (p < 1) requestAnimationFrame(step);
          else { clearTimeout(settle); busy = false; }
        };
        requestAnimationFrame(step);
      };
      const draw = () => {
        wrap.classList.add("drawn");
        if (built) built.base.style.strokeDashoffset = 0;
        setTimeout(count, reduced ? 0 : 950);
      };
      setTimeout(draw, reduced ? 0 : 1000); /* after the verb letters land */
      if (hoverable && !reduced) word.addEventListener("pointerenter", () => {
        count();
        log("re_measure", { word: word.textContent.trim() });
      });
      let rt;
      addEventListener("resize", () => {
        clearTimeout(rt);
        rt = setTimeout(() => {
          built = buildRuler(wrap);
          if (built && wrap.classList.contains("drawn")) built.base.style.strokeDashoffset = 0;
          if (out && !busy) out.textContent = "= " + width() + "px · verified";
        }, 180);
      }, { passive: true });
    });
  }

  /* ── THE MEASURING FIELD ─────────────────────────────────────────────
     A constellation of drifting points behind the hero. Points near each
     other link faintly; points near the pointer link to it like survey
     lines, and the nearest line reads out its length. Colors are READ
     from the tokens (never defined here); re-read on theme change.
     Perf: DPR≤2, pauses when offscreen or the tab hides; reduced motion
     renders one static frame; touch devices get the drift, no pointer. */
  function field() {
    const host = $(".stage--field");
    if (!host) return;
    const atmos = $(".atmos", host) || host;
    const cvs = document.createElement("canvas");
    cvs.className = "field-canvas"; /* NOT "field" — that's the form component */
    cvs.setAttribute("aria-hidden", "true");
    atmos.appendChild(cvs);
    const ctx = cvs.getContext("2d");
    let W = 0, Hh = 0, parts = [], pal = [], mono = "";
    const ptr = { x: -1e4, y: -1e4 };

    function readTokens() {
      const cs = getComputedStyle(document.documentElement);
      const grab = n => cs.getPropertyValue(n).trim();
      pal = [grab("--build"), grab("--measure"), grab("--text-mid")];
      mono = grab("--mono") || "monospace";
    }
    function rgba(hex, a) {
      const n = parseInt(hex.slice(1), 16);
      return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
    }
    function size() {
      const r = host.getBoundingClientRect();
      const dpr = Math.min(2, devicePixelRatio || 1);
      const pW = W, pH = Hh;
      W = Math.max(1, r.width); Hh = Math.max(1, r.height);
      cvs.width = W * dpr; cvs.height = Hh * dpr;
      cvs.style.width = W + "px"; cvs.style.height = Hh + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      /* rescale, don't reshuffle (mobile URL-bar resizes would jolt) */
      if (parts.length && pW > 1 && pH > 1) {
        const sx = W / pW, sy = Hh / pH;
        parts.forEach(p => { p.x *= sx; p.y *= sy; });
      } else seed();
    }
    function seed() {
      const n = Math.min(72, Math.round((W * Hh) / 16000));
      parts = Array.from({ length: n }, () => ({
        x: Math.random() * W, y: Math.random() * Hh,
        vx: (Math.random() - 0.5) * 0.18, vy: (Math.random() - 0.5) * 0.18,
        r: 0.8 + Math.random() * 1.0,
        c: Math.random() < 0.42 ? 0 : (Math.random() < 0.75 ? 1 : 2)
      }));
    }
    function frame(move) {
      ctx.clearRect(0, 0, W, Hh);
      /* faint peer links */
      for (let i = 0; i < parts.length; i++) {
        const a = parts[i];
        if (move) {
          a.x += a.vx; a.y += a.vy;
          if (a.x < -10) a.x = W + 10; if (a.x > W + 10) a.x = -10;
          if (a.y < -10) a.y = Hh + 10; if (a.y > Hh + 10) a.y = -10;
        }
        for (let j = i + 1; j < parts.length; j++) {
          const b = parts[j];
          const dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
          if (d2 < 8100) {
            const d = Math.sqrt(d2);
            ctx.strokeStyle = rgba(pal[a.c], (1 - d / 90) * 0.13);
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
      }
      /* survey lines to the pointer + nearest distance readout */
      let nearest = null, nd = 1e9;
      if (ptr.x > -1e3) {
        for (const p of parts) {
          const dx = p.x - ptr.x, dy = p.y - ptr.y, d2 = dx * dx + dy * dy;
          if (d2 < 24025) {
            const d = Math.sqrt(d2);
            ctx.strokeStyle = rgba(pal[1], (1 - d / 155) * 0.4);
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(ptr.x, ptr.y); ctx.stroke();
            if (d < nd && d > 24) { nd = d; nearest = p; }
          }
        }
        /* crosshair */
        ctx.strokeStyle = rgba(pal[1], 0.55);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(ptr.x, ptr.y, 9, 0, Math.PI * 2); ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(ptr.x - 15, ptr.y); ctx.lineTo(ptr.x - 5, ptr.y);
        ctx.moveTo(ptr.x + 5, ptr.y); ctx.lineTo(ptr.x + 15, ptr.y);
        ctx.moveTo(ptr.x, ptr.y - 15); ctx.lineTo(ptr.x, ptr.y - 5);
        ctx.moveTo(ptr.x, ptr.y + 5); ctx.lineTo(ptr.x, ptr.y + 15);
        ctx.stroke();
        if (nearest) {
          const mx = (nearest.x + ptr.x) / 2, my = (nearest.y + ptr.y) / 2;
          ctx.font = "500 9px " + mono;
          ctx.fillStyle = rgba(pal[1], 0.75);
          ctx.fillText(Math.round(nd) + "px", mx + 6, my - 5);
        }
      }
      /* points */
      for (const p of parts) {
        ctx.fillStyle = rgba(pal[p.c], p.c === 2 ? 0.5 : 0.8);
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
      }
    }
    let running = false, visible = true, gen = 0;
    /* generation token: a paused rAF callback from a hidden tab must not
       resurrect its old chain next to a freshly started one */
    function loop(g) {
      if (!running || g !== gen) return;
      frame(true);
      requestAnimationFrame(() => loop(g));
    }
    function setRunning(on) {
      const next = on && visible && !document.hidden && !reduced;
      if (next && !running) { running = true; gen++; requestAnimationFrame(() => loop(gen)); }
      else if (!next) running = false;
    }
    readTokens(); size();
    if (reduced) { frame(false); }
    else {
      const io = "IntersectionObserver" in window
        ? new IntersectionObserver(es => { visible = es[es.length - 1].isIntersecting; setRunning(true); }, { threshold: 0.02 })
        : null;
      if (io) io.observe(host); else setRunning(true);
      document.addEventListener("visibilitychange", () => setRunning(true));
      setRunning(true);
      frame(false); /* first frame even if frozen/offscreen */
    }
    if (hoverable && !reduced) {
      host.addEventListener("pointermove", e => {
        const r = host.getBoundingClientRect();
        ptr.x = e.clientX - r.left; ptr.y = e.clientY - r.top;
      }, { passive: true });
      host.addEventListener("pointerleave", () => { ptr.x = -1e4; ptr.y = -1e4; });
    }
    let rt;
    addEventListener("resize", () => {
      clearTimeout(rt);
      rt = setTimeout(() => { size(); if (!running) frame(false); }, 180); /* repaint static/paused frames */
    }, { passive: true });
    new MutationObserver(() => { readTokens(); if (!running) frame(false); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  /* ── event wiring ── */
  function wire() {
    document.querySelectorAll(".tool-card").forEach(card => {
      let last = 0;
      card.addEventListener("mouseenter", () => {
        const now = Date.now();
        if (now - last < 5000) return;
        last = now;
        log("tool_hover", { id: card.dataset.tool || "?" });
      });
    });
    document.querySelectorAll(".btn").forEach(b =>
      b.addEventListener("click", () => log("cta_click", { label: b.textContent.trim().toLowerCase().slice(0, 22) })));
    document.querySelectorAll(".morph").forEach(m => {
      let done = false;
      m.addEventListener("mouseenter", () => {
        if (done) return; done = true;
        const b = $(".morph__b", m);
        if (b) log("refocus", { to: b.textContent.trim().replace(/\.$/, "") });
      });
    });
    const ex = $("#expandAll");
    if (ex) ex.addEventListener("click", () => log("cv_expand", { mode: ex.textContent.includes("collapse") ? "all" : "none" }));
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  let rulersStarted = false;
  function rulersOnce() { if (rulersStarted) return; rulersStarted = true; rulers(); }
  function init() {
    mountTicker(); reveals(); pointerFX(); field(); wire();
    /* the ruler measures TYPE — never before the webfont has swapped in */
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(rulersOnce);
      setTimeout(rulersOnce, 2500); /* fallback if ready never resolves */
    } else rulersOnce();
  }
})();
