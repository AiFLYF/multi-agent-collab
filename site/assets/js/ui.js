/* ============================================================
 * ui.js — 页面交互层：预加载幕布 / 导航 / 揭示动画 / 计数器 /
 *          卡片倾斜 / 代码复制 / 标签页 / 自定义光标
 * 无依赖，file:// 与 GitHub Pages 均可运行。
 * ============================================================ */
(function () {
  'use strict';

  var root = document.documentElement;
  var body = document.body;
  root.className += ' js';
  if (location.protocol === 'file:') body.className += ' from-file';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };

  /* ── 顶栏时钟 ────────────────────────────────────────────── */
  var clockEl = $('#clock');
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function tickClock() {
    var d = new Date();
    if (clockEl) clockEl.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }
  tickClock();
  setInterval(tickClock, 1000);

  /* ── 预加载幕布 ──────────────────────────────────────────── */
  var preCount = $('#preCount');
  var preBar = $('#preBar');
  var preDotsBox = $('#preDots');
  var dots = [];
  if (preDotsBox) {
    for (var i = 0; i < 6; i++) {
      var d = document.createElement('i');
      preDotsBox.appendChild(d);
      dots.push(d);
    }
  }
  var booted = false;
  var preStart = performance.now();
  function setPre(p) {
    if (preCount) preCount.textContent = ('0' + Math.floor(p * 100)).slice(-2);
    if (preBar) preBar.style.transform = 'scaleX(' + p + ')';
    var lit = Math.floor(p * dots.length);
    for (var k = 0; k < dots.length; k++) dots[k].classList.toggle('on', k < lit);
  }
  function revealPage() {
    if (booted) return;
    booted = true;
    setPre(1);
    body.classList.add('is-loaded');
  }
  function preLoop() {
    if (booted) return;
    var t = (performance.now() - preStart) / 1000;
    var sceneReady = window.__sceneBoot === 'ready' || window.__sceneBoot === 'partial';
    // 缓慢爬升的假进度（永不静止，用户不会以为卡死）
    var p = Math.min(t / (t + 1.6), 0.92);
    setPre(p);
    if (sceneReady || t > 4.5 || reduceMotion) {
      revealPage();
      return;
    }
    requestAnimationFrame(preLoop);
  }
  requestAnimationFrame(preLoop);

  /* ── 滚动：进度条 + 顶栏实心化 ───────────────────────────── */
  var progressBar = $('#progressBar');
  var head = $('#siteHead');
  function onScroll() {
    var y = window.scrollY || window.pageYOffset;
    var h = document.documentElement.scrollHeight - window.innerHeight;
    if (progressBar) progressBar.style.transform = 'scaleX(' + (h > 0 ? Math.min(y / h, 1) : 0) + ')';
    if (head) head.classList.toggle('solid', y > 24);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ── 章节导航（顶栏 / 侧索引 / 菜单 / 按钮） ──────────────── */
  var sections = $$('section.sec[id]');
  function scrollToSection(idx) {
    var el = sections[idx];
    if (el) el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
  }
  $$('[data-target]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      closeMenu();
      scrollToSection(parseInt(btn.getAttribute('data-target'), 10));
    });
  });
  function syncActiveNav() {
    var y = window.scrollY + window.innerHeight * 0.38;
    var cur = 0;
    sections.forEach(function (s, i) { if (s.offsetTop <= y) cur = i; });
    $$('.site-nav a, .side-index button').forEach(function (a) {
      a.classList.toggle('on', parseInt(a.getAttribute('data-target'), 10) === cur);
    });
  }
  window.addEventListener('scroll', syncActiveNav, { passive: true });
  syncActiveNav();

  /* ── 移动菜单 ────────────────────────────────────────────── */
  var burger = $('#burger');
  var menu = $('#menu');
  function closeMenu() {
    if (!menu) return;
    menu.classList.remove('open');
    if (burger) { burger.classList.remove('open'); burger.setAttribute('aria-expanded', 'false'); }
  }
  if (burger && menu) {
    burger.addEventListener('click', function () {
      var open = menu.classList.toggle('open');
      burger.classList.toggle('open', open);
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }
  /* ── 揭示动画 ────────────────────────────────────────────── */
  var rvEls = $$('.rv');
  if ('IntersectionObserver' in window && !reduceMotion) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    rvEls.forEach(function (el) { io.observe(el); });
    /* 兜底：瞬时滚动可能整体跳过某些元素（IO 不触发），滚动时统一巡检 */
    window.addEventListener('scroll', function () {
      var vh = window.innerHeight;
      rvEls.forEach(function (el) {
        if (!el.classList.contains('in') && el.getBoundingClientRect().top < vh * 0.92) {
          el.classList.add('in');
          io.unobserve(el);
        }
      });
    }, { passive: true });
  } else {
    rvEls.forEach(function (el) { el.classList.add('in'); });
  }

  /* ── 数字滚动 ────────────────────────────────────────────── */
  var counters = $$('[data-count]');
  function runCounter(el) {
    var target = parseInt(el.getAttribute('data-count'), 10) || 0;
    if (reduceMotion || target === 0) { el.textContent = String(target); return; }
    var t0 = performance.now(), dur = 1100;
    function step(t) {
      var k = Math.min((t - t0) / dur, 1);
      var e = 1 - Math.pow(1 - k, 3);
      el.textContent = String(Math.round(target * e));
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  if ('IntersectionObserver' in window) {
    var cio = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { runCounter(en.target); cio.unobserve(en.target); }
      });
    }, { threshold: 0.6 });
    counters.forEach(function (el) { cio.observe(el); });
  } else {
    counters.forEach(runCounter);
  }

  /* ── 卡片 3D 倾斜 + 光斑 ─────────────────────────────────── */
  if (!reduceMotion && window.matchMedia('(pointer: fine)').matches) {
    $$('[data-tilt]').forEach(function (card) {
      card.addEventListener('pointermove', function (e) {
        var r = card.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width;
        var py = (e.clientY - r.top) / r.height;
        card.style.setProperty('--mx', (px * 100) + '%');
        card.style.setProperty('--my', (py * 100) + '%');
        var rx = (py - 0.5) * -3.2, ry = (px - 0.5) * 3.2;
        card.style.transform = 'translateY(-4px) perspective(900px) rotateX(' + rx + 'deg) rotateY(' + ry + 'deg)';
      });
      card.addEventListener('pointerleave', function () { card.style.transform = ''; });
    });
  }

  /* ── 复制（代码块 + 命令表） ─────────────────────────────── */
  function copyText(text, btn) {
    function ok() {
      if (!btn) return;
      var old = btn.textContent;
      btn.textContent = '已复制 ✓';
      btn.style.color = 'var(--lm)';
      setTimeout(function () { btn.textContent = old; btn.style.color = ''; }, 1400);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(ok, function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); ok(); } catch (e) { /* 忽略 */ }
      document.body.removeChild(ta);
    }
  }
  $$('.copy[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var src = document.getElementById(btn.getAttribute('data-copy'));
      if (src) copyText(src.textContent, btn);
    });
  });
  $$('.cmd-table td.cmd[data-copy]').forEach(function (td) {
    td.addEventListener('click', function () { copyText(td.getAttribute('data-copy'), null); });
  });

  /* ── 标签页 ──────────────────────────────────────────────── */
  var tabs = $$('.tab');
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.classList.toggle('is-on', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      $$('.tabpanel').forEach(function (p) {
        p.classList.toggle('is-on', p.getAttribute('data-panel') === tab.getAttribute('data-tab'));
      });
    });
  });

  /* ── 看板截图放大 ────────────────────────────────────────── */
  var lightbox = $('#lightbox');
  var lbClose = $('#lbClose');
  var lbTrigger = null;
  function openLightbox(trigger) {
    if (!lightbox) return;
    lbTrigger = trigger || null;
    lightbox.hidden = false;
    body.classList.add('lb-open');
    if (lbClose) lbClose.focus();
  }
  function closeLightbox() {
    if (!lightbox || lightbox.hidden) return;
    lightbox.hidden = true;
    body.classList.remove('lb-open');
    if (lbTrigger) lbTrigger.focus();
    lbTrigger = null;
  }
  $$('[data-zoom]').forEach(function (el) {
    el.addEventListener('click', function () { openLightbox(el); });
  });
  if (lightbox) {
    lightbox.addEventListener('click', function (e) {
      if (e.target === lightbox) closeLightbox();      /* 点背景关闭，点图不关 */
    });
  }
  if (lbClose) lbClose.addEventListener('click', closeLightbox);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    closeLightbox();
    closeMenu();
  });

  /* ── 自定义光标 ──────────────────────────────────────────── */
  var dot = $('.c-dot'), ring = $('.c-ring');
  if (window.matchMedia('(pointer: fine)').matches && !reduceMotion) {
    var rx = 0, ry = 0, tx = 0, ty = 0;
    document.addEventListener('pointermove', function (e) {
      tx = e.clientX; ty = e.clientY;
      if (dot) { dot.style.left = tx + 'px'; dot.style.top = ty + 'px'; }
    });
    (function ringLoop() {
      rx += (tx - rx) * 0.16; ry += (ty - ry) * 0.16;
      if (ring) { ring.style.left = rx + 'px'; ring.style.top = ry + 'px'; }
      requestAnimationFrame(ringLoop);
    })();
    $$('a, button, [data-hover], .cmd-table td.cmd').forEach(function (el) {
      el.addEventListener('pointerenter', function () { if (ring) ring.classList.add('hot'); });
      el.addEventListener('pointerleave', function () { if (ring) ring.classList.remove('hot'); });
    });
  } else if (dot && ring) {
    dot.style.display = 'none'; ring.style.display = 'none';
  }
})();
