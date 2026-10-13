/* ============================================================
 * ui.js — 页面交互层：预加载幕布 / 导航 / 揭示动画 / 计数器 /
 *          卡片倾斜 / 代码复制 / 标签页 / 灯箱 / 自定义光标
 * 无依赖，file:// 与 GitHub Pages 均可运行。
 * ============================================================ */
(function () {
  'use strict';

  var root = document.documentElement;
  var body = document.body;
  root.className += ' js';
  body.className += ' is-booting';              /* 幕布期间锁滚动 */
  if (location.protocol === 'file:') body.className += ' from-file';

  /* 解锁兜底放在最前面：后面任何一步抛异常，都不许把页面关在幕布后面 */
  var unlocked = false;
  function unlockPage() {
    if (unlocked) return;
    unlocked = true;
    body.classList.remove('is-booting');
    body.classList.add('is-loaded');
  }
  setTimeout(unlockPage, 4000);

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };
  var srLive = $('#srLive');
  function announce(msg) {
    if (!srLive) return;
    srLive.textContent = '';
    setTimeout(function () { srLive.textContent = msg; }, 40);
  }

  /* ── 顶栏时钟 ────────────────────────────────────────────── */
  var clockEl = $('#clock');
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function tickClock() {
    var d = new Date();
    if (clockEl) clockEl.textContent = pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }
  tickClock();
  setInterval(tickClock, 1000);

  /* ── 预加载幕布 ────────────────────────────────────────────
   * 场景（hero/topo）在同一批 defer 里同步就绪，如果立刻掀幕只会闪一下，
   * 观众连字都没看清。所以设一个「最短展示时长」，并让进度永不停：
   * 前段线性爬到 0.9，之后按指数继续缓慢爬升 —— 看着像在干活，不像卡死。
   * 任何异常都有兜底，绝不把页面关在幕布后面。
   */
  var PRELOAD_MIN = 900;                 /* 最短展示时长，与 CSS 的 --pre-dur 一致 */
  var PRELOAD_MAX = 2600;                /* 场景迟迟不就绪的兜底 */
  var preCount = $('#preCount');
  var preStageEl = $('#preStage');
  (function buildDots() {
    var box = $('#preDots');
    if (!box) return;
    for (var i = 0; i < 6; i++) box.appendChild(document.createElement('i'));
  })();

  var STAGES = [
    [0.00, 'BOOTING · 初始化画布'],
    [0.20, 'SYNCING · 接入六个信号源'],
    [0.50, 'CONVERGING · 汇入共享黑板'],
    [0.82, 'READY · 规矩就位']
  ];
  var curStage = '';
  /* 进度环 / 进度条 / 六个信号点由 CSS 动画驱动（见 style.css 的 --pre-dur），
     这里只更新数字与阶段文案 —— 主线程被脚本占住时，视觉也不会停。 */
  function setPre(p) {
    p = Math.max(0, Math.min(1, p));
    if (preCount) preCount.textContent = pad2(Math.round(p * 100));
    for (var s = 0; s < STAGES.length; s++) {
      if (p >= STAGES[s][0] && curStage !== STAGES[s][1]) {
        curStage = STAGES[s][1];
        if (preStageEl) preStageEl.textContent = curStage;
      }
    }
  }
  var booted = false;
  var preTimer = null;
  var preStart = performance.now();
  var firstFrameAt = null;      /* 第一帧真正画出来的时刻 */
  requestAnimationFrame(function () { firstFrameAt = performance.now(); });
  function revealPage() {
    if (booted) return;
    booted = true;
    if (preTimer) { clearInterval(preTimer); preTimer = null; }
    setPre(1);
    unlockPage();
  }
  /* 进度用 setInterval 驱动而不是 requestAnimationFrame：rAF 在后台标签页
     会完全停摆；定时器最差只是降频，对「加载指示器」来说必须一直在走。
     而「最短展示时长」从第一帧真正画出来开始算 —— CSS 进度动画也是那一刻
     起跑的，两者对齐，首屏绘制再晚也一定看得到进度走完。 */
  function preTick() {
    if (booted) return;
    var now = performance.now();
    var elapsed = now - preStart;                                   /* 脚本已运行多久 */
    var shown = firstFrameAt ? now - firstFrameAt : elapsed;        /* 幕布已可见多久 */
    var ready = window.__sceneBoot === 'ready' || window.__sceneBoot === 'partial';
    var p = shown < PRELOAD_MIN
      ? (shown / PRELOAD_MIN) * 0.9
      : 0.9 + 0.1 * (1 - Math.exp(-(shown - PRELOAD_MIN) / 2600));
    setPre(p);
    if ((ready && shown >= PRELOAD_MIN) ||
        (!ready && elapsed >= PRELOAD_MAX) ||
        (reduceMotion && shown > 160)) {
      revealPage();
    }
  }
  setPre(0);
  preTimer = setInterval(preTick, 40);
  setTimeout(revealPage, PRELOAD_MAX + 1800);    /* 再兜一层：确保进度条走到 100% */

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
  /* 每章一个强调色（section 上的 data-acc / data-sec）：
     滚到哪一章，顶栏高亮、侧索引、进度条末端与章节编号就换成那一章的颜色，
     给读者一个「你在哪儿」的持续线索。 */
  var curAccent = '';
  function syncActiveNav() {
    var y = window.scrollY + window.innerHeight * 0.38;
    var cur = 0;
    sections.forEach(function (s, i) { if (s.offsetTop <= y) cur = i; });
    var active = sections[cur];
    var idx = active ? parseInt(active.getAttribute('data-sec'), 10) : 0;
    if (isNaN(idx)) idx = cur;
    $$('.site-nav a, .side-index button').forEach(function (a) {
      a.classList.toggle('on', parseInt(a.getAttribute('data-target'), 10) === idx);
    });
    var acc = active && active.getAttribute('data-acc');
    if (acc && acc !== curAccent) {
      curAccent = acc;
      root.style.setProperty('--acc', acc);
    }
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
    /* 点菜单外区域也收起 */
    document.addEventListener('click', function (e) {
      if (!menu.classList.contains('open')) return;
      if (menu.contains(e.target) || burger.contains(e.target)) return;
      closeMenu();
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

  /* ── 复制（代码块按钮 + 命令表单元格） ─────────────────────
   * 按钮显示「已复制 ✓」；表格单元格本身没有按钮结构，
   * 改用 .copied 类改写 ::after，同时给读屏报一句。
   */
  function copyText(text, btn) {
    function ok() {
      if (btn) {
        var old = btn.textContent;
        btn.textContent = '已复制 ✓';
        btn.style.color = 'var(--cy)';
        setTimeout(function () { btn.textContent = old; btn.style.color = ''; }, 1400);
      }
      announce('已复制到剪贴板');
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
    td.setAttribute('role', 'button');
    td.setAttribute('tabindex', '0');
    var timer = null;
    function flash() {
      clearTimeout(timer);
      td.classList.add('copied');
      timer = setTimeout(function () { td.classList.remove('copied'); }, 1400);
    }
    td.addEventListener('click', function () { copyText(td.getAttribute('data-copy'), null); flash(); });
    td.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); td.click(); }
    });
  });

  /* ── 标签页（点击 + 左右方向键，符合 ARIA tabs 模式） ────── */
  var tabs = $$('.tab');
  var panels = $$('.tabpanel');
  function selectTab(i, focus) {
    tabs.forEach(function (t, k) {
      var on = k === i;
      t.classList.toggle('is-on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.setAttribute('tabindex', on ? '0' : '-1');
    });
    panels.forEach(function (p) {
      p.classList.toggle('is-on', p.getAttribute('data-panel') === tabs[i].getAttribute('data-tab'));
    });
    if (focus) tabs[i].focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { selectTab(i, false); });
    tab.addEventListener('keydown', function (e) {
      var d = e.key === 'ArrowRight' ? 1 : (e.key === 'ArrowLeft' ? -1 : 0);
      if (!d) return;
      e.preventDefault();
      selectTab((i + d + tabs.length) % tabs.length, true);
    });
  });
  selectTab(0, false);

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
    announce('看板截图已放大，按 Escape 关闭');
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
  /* Esc 关弹层；灯箱打开时把 Tab 圈在里面 */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeLightbox(); closeMenu(); return; }
    if (e.key !== 'Tab' || !lightbox || lightbox.hidden) return;
    var f = $$('button, [href], [tabindex]:not([tabindex="-1"])', lightbox);
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
    else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
  });

  /* ── 第 05 章的 WebGL 按需加载 ─────────────────────────────
   * three.js 是 712KB，放在首屏关键路径上会占住主线程（实测把加载
   * 幕布的计数都卡成 00）。改成滚到第 05 章前 1.5 屏才开始加载，
   * 首屏只剩 ui.js + hero.js 两个小文件。
   */
  var topoState = 'idle';
  function loadTopo() {
    if (topoState !== 'idle') return;
    topoState = 'loading';
    function injectTopo() {
      var t = document.createElement('script');
      t.src = 'assets/js/topo.js';
      t.onerror = function () { document.documentElement.className += ' no-webgl'; };
      document.body.appendChild(t);
    }
    var local = document.createElement('script');
    local.src = 'vendor/three/build/three.min.js';
    local.onload = function () { topoState = 'ready'; injectTopo(); };
    local.onerror = function () {                     /* 本地缺失时退回 CDN */
      var cdn = document.createElement('script');
      cdn.src = 'https://cdn.jsdelivr.net/npm/three@0.149.0/build/three.min.js';
      cdn.onload = function () { topoState = 'ready'; injectTopo(); };
      cdn.onerror = function () { topoState = 'failed'; document.documentElement.className += ' no-webgl'; };
      document.body.appendChild(cdn);
    };
    document.body.appendChild(local);
  }
  var sec5 = document.getElementById('sec-5');
  if (sec5 && 'IntersectionObserver' in window) {
    var tio = new IntersectionObserver(function (es) {
      if (es[0].isIntersecting) { tio.disconnect(); loadTopo(); }
    }, { rootMargin: '150% 0px' });
    tio.observe(sec5);
  } else {
    loadTopo();
  }

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
