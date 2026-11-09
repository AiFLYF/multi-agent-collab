/* ============================================================
 * ui.js — 导航、复制、标签页、灯箱与按需动效。
 * 无依赖，file:// 与 GitHub Pages 均可运行。
 * ============================================================ */
(function () {
  'use strict';

  var root = document.documentElement;
  var body = document.body;
  root.className += ' js';
  body.classList.add('is-loaded');
  if (location.protocol === 'file:') body.className += ' from-file';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };
  var srLive = $('#srLive');
  function announce(msg) {
    if (!srLive) return;
    srLive.textContent = '';
    setTimeout(function () { srLive.textContent = msg; }, 40);
  }

  var installData = window.MAC_INSTALL;
  if (installData) {
    $$('[data-kit-version]').forEach(function (el) { el.textContent = 'V' + installData.version; });
    $$('[data-platform-count]').forEach(function (el) { el.textContent = String(installData.platforms.length); });
  }

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
    if (!el) return;
    el.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
    if (window.history && history.replaceState) history.replaceState(null, '', '#' + el.id);
    el.setAttribute('tabindex', '-1');
    el.focus({ preventScroll: true });
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
      var on = parseInt(a.getAttribute('data-target'), 10) === idx;
      a.classList.toggle('on', on);
      if (on) a.setAttribute('aria-current', 'true');
      else a.removeAttribute('aria-current');
    });
    var acc = active && active.getAttribute('data-acc');
    if (acc && acc !== curAccent) {
      curAccent = acc;
      root.style.setProperty('--acc', acc);
    }
  }
  window.addEventListener('scroll', syncActiveNav, { passive: true });
  syncActiveNav();
  /* 侧索引与顶栏导航指向完全相同的 7 个章节，不该让键盘用户多穿 7 个站：
     保留鼠标可点与读屏可读，但移出 Tab 序 */
  $$('.side-index button').forEach(function (b) { b.setAttribute('tabindex', '-1'); });

  /* ── 移动菜单 ──────────────────────────────────────────────
   * 菜单是 role="dialog"，开合必须同步 aria-hidden —— 否则读屏被告知
   * "忽略这块内容"，键盘却把焦点送了进去（axe 的 aria-hidden-focus 违规，
   * 也违反 WCAG 4.1.2）。打开时焦点移进第一个链接，关闭时还给触发者，
   * 期间 Tab 在菜单内循环。
   */
  var burger = $('#burger');
  var menu = $('#menu');
  var menuClose = $('#menuClose');
  var menuReturnFocus = null;
  function menuLinks() { return $$('button, a[href]', menu); }
  function menuIsOpen() { return !!menu && menu.classList.contains('open'); }
  function openMenu() {
    if (!menu || menuIsOpen()) return;
    menuReturnFocus = document.activeElement;
    menu.removeAttribute('inert');
    menu.classList.add('open');
    body.classList.add('menu-open');
    menu.setAttribute('aria-hidden', 'false');
    if (burger) { burger.classList.add('open'); burger.setAttribute('aria-expanded', 'true'); burger.setAttribute('aria-label', '关闭菜单'); }
    setBackgroundInert(true, menu);
    /* 必须延后一帧再 focus：刚加上 .open 时样式还没重算，
       此刻菜单的 visibility 仍是 hidden，同步 focus() 会被浏览器拒绝 */
    function focusFirst() {
      if (!menuIsOpen()) return;
      var ae = document.activeElement;
      if (ae && ae !== burger && ae !== document.body && menu.contains(ae)) return;
      var f = menuLinks()[0];
      if (f) f.focus();
    }
    requestAnimationFrame(focusFirst);
    setTimeout(focusFirst, 80);
  }
  function closeMenu(restoreFocus) {
    if (!menu) return;
    var wasOpen = menuIsOpen();
    menu.classList.remove('open');
    body.classList.remove('menu-open');
    menu.setAttribute('aria-hidden', 'true');
    menu.setAttribute('inert', '');
    if (burger) { burger.classList.remove('open'); burger.setAttribute('aria-expanded', 'false'); burger.setAttribute('aria-label', '打开菜单'); }
    if (wasOpen) setBackgroundInert(false);
    if (wasOpen && restoreFocus === true) {
      var back = menuReturnFocus && document.contains(menuReturnFocus) ? menuReturnFocus : burger;
      if (back) back.focus();
    }
    if (!wasOpen) return;
    menuReturnFocus = null;
  }
  if (burger && menu) {
    burger.addEventListener('click', function () {
      if (menuIsOpen()) closeMenu(false); else openMenu();
    });
    /* 点菜单外区域也收起 */
    document.addEventListener('click', function (e) {
      if (!menuIsOpen()) return;
      if (menu.contains(e.target) || burger.contains(e.target)) return;
      closeMenu(true);
    });
  }
  if (menuClose) menuClose.addEventListener('click', function () { closeMenu(true); });
  if (menu) $$('a', menu).forEach(function (link) {
    if (!link.hasAttribute('data-target')) link.addEventListener('click', function () { closeMenu(false); });
  });
  window.addEventListener('resize', function () {
    if (window.innerWidth > 980 && menuIsOpen()) closeMenu(true);
  });

  /* 正文和事实数字立即呈现，不等待滚动或动画。 */
  $$('.rv').forEach(function (el) { el.classList.add('in'); });
  $$('[data-count]').forEach(function (el) { el.textContent = el.getAttribute('data-count'); });

  /* ── 复制（代码块按钮 + 命令表单元格） ─────────────────────
   * 按钮显示「已复制 ✓」；表格单元格本身没有按钮结构，
   * 改用 .copied 类改写 ::after，同时给读屏报一句。
   */
  var copyNotice = $('#copyNotice');
  var noticeTimer;
  function copyText(text, btn) {
    function feedback(message, failed) {
      if (copyNotice) {
        clearTimeout(noticeTimer);
        copyNotice.hidden = false;
        copyNotice.dataset.kind = failed ? 'error' : 'success';
        copyNotice.textContent = failed ? '复制失败，请选择代码文字手动复制。' : '已复制到剪贴板。';
        noticeTimer = setTimeout(function () { copyNotice.hidden = true; }, failed ? 8000 : 3800);
      }
      if (!btn) return;
      if (!btn.dataset.copyLabel) btn.dataset.copyLabel = btn.textContent;
      clearTimeout(btn.copyTimer);
      btn.textContent = message;
      btn.dataset.copyState = failed ? 'error' : 'success';
      btn.copyTimer = setTimeout(function () { btn.textContent = btn.dataset.copyLabel; delete btn.dataset.copyState; }, 2600);
    }
    function ok() {
      feedback('已复制 ✓', false);
      if (!copyNotice) announce('已复制到剪贴板');
    }
    function fail() {
      feedback('请手动复制', true);
      if (!copyNotice) announce('未能写入剪贴板，请手动选择文本复制');
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { navigator.clipboard.writeText(text).then(ok, fallback); } catch (e) { fallback(); }
    } else { fallback(); }
    function fallback() {
      var previousFocus = document.activeElement;
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-10000px';
      ta.style.opacity = '0';
      ta.setAttribute('readonly', '');
      document.body.appendChild(ta);
      ta.select();
      try { if (document.execCommand('copy')) ok(); else fail(); } catch (e) { fail(); }
      document.body.removeChild(ta);
      if (previousFocus && previousFocus.focus) previousFocus.focus();
    }
  }
  $$('.copy[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var src = document.getElementById(btn.getAttribute('data-copy'));
      if (src) copyText(src.textContent, btn);
    });
  });
  $$('.cmd-table td.cmd[data-copy]').forEach(function (td) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'command-copy';
    button.textContent = td.textContent;
    button.setAttribute('aria-label', '复制命令：' + td.getAttribute('data-copy'));
    td.textContent = '';
    td.appendChild(button);
    button.addEventListener('click', function () { copyText(td.getAttribute('data-copy'), button); });
  });

  /* ── 标签页（点击 + 左右方向键，符合 ARIA tabs 模式） ────── */
  var tabs = $$('.tab');
  var panels = $$('.tabpanel');
  function selectTab(i, focus) {
    if (!tabs.length || !tabs[i]) return;
    tabs.forEach(function (t, k) {
      var on = k === i;
      t.classList.toggle('is-on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.setAttribute('tabindex', on ? '0' : '-1');
    });
    panels.forEach(function (p) {
      var on = p.getAttribute('data-panel') === tabs[i].getAttribute('data-tab');
      p.classList.toggle('is-on', on);
      p.hidden = !on;
      p.setAttribute('tabindex', '0');
    });
    if (focus) tabs[i].focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { selectTab(i, false); });
    tab.addEventListener('keydown', function (e) {
      if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault(); selectTab(e.key === 'Home' ? 0 : tabs.length - 1, true); return;
      }
      var d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : (e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0);
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
  /* 灯箱打开时把背景整块 inert：hidden 只挡住了它自己，
     背后的正文对读屏虚拟光标依然可达 */
  var inertBackground = [];
  function setBackgroundInert(on, dialog) {
    if (on) {
      inertBackground = [];
      Array.prototype.forEach.call(document.body.children, function (el) {
        if (el === dialog || el.tagName === 'SCRIPT' || el === srLive || el === copyNotice) return;
        inertBackground.push({element: el, wasInert: el.hasAttribute('inert')});
        el.setAttribute('inert', '');
      });
    } else {
      inertBackground.forEach(function (entry) {
        if (!entry.wasInert) entry.element.removeAttribute('inert');
      });
      inertBackground = [];
    }
  }
  function openLightbox(trigger) {
    if (!lightbox) return;
    lbTrigger = trigger || null;
    lightbox.hidden = false;
    body.classList.add('lb-open');
    closeMenu(false);
    setBackgroundInert(true, lightbox);
    if (lbClose) lbClose.focus();
    announce('看板截图已放大，按 Escape 关闭');
  }
  function closeLightbox() {
    if (!lightbox || lightbox.hidden) return;
    lightbox.hidden = true;
    body.classList.remove('lb-open');
    setBackgroundInert(false);
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
  /* Esc 关弹层；弹层打开时把 Tab 圈在里面（菜单与灯箱共用一套逻辑） */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeLightbox(); closeMenu(true); return; }
    if (e.key !== 'Tab') return;
    var scope = menuIsOpen() ? menu : ((lightbox && !lightbox.hidden) ? lightbox : null);
    if (!scope) return;
    var f = $$('a[href], button, [tabindex]:not([tabindex="-1"])', scope).filter(function (el) {
      return el.getClientRects().length > 0;
    });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (!scope.contains(document.activeElement)) { first.focus(); e.preventDefault(); }
    else if (e.shiftKey && document.activeElement === first) { last.focus(); e.preventDefault(); }
    else if (!e.shiftKey && document.activeElement === last) { first.focus(); e.preventDefault(); }
  });

  /* 用户选择后才加载自带的画布资源，失败时保留静态内容。 */
  function loadScript(source, ready, failed) {
    var script = document.createElement('script');
    script.src = source;
    script.onload = ready;
    script.onerror = failed;
    document.body.appendChild(script);
  }
  var motionToggle = $('#motionToggle');
  var motionState = 'idle';
  if (motionToggle) motionToggle.addEventListener('click', function () {
    if (motionState === 'loading') return;
    if (motionState === 'ready') {
      var active = body.classList.toggle('motion-active');
      motionToggle.setAttribute('aria-pressed', String(active));
      motionToggle.textContent = active ? '关闭背景动效' : '开启背景动效';
      return;
    }
    motionState = 'loading';
    motionToggle.disabled = true;
    motionToggle.textContent = '正在加载…';
    body.classList.add('motion-active');
    loadScript('assets/js/hero.js', function () {
      motionState = 'ready';
      motionToggle.disabled = false;
      motionToggle.setAttribute('aria-pressed', 'true');
      motionToggle.textContent = '关闭背景动效';
    }, function () {
      motionState = 'idle';
      body.classList.remove('motion-active');
      motionToggle.disabled = false;
      motionToggle.textContent = '重新加载背景动效';
      announce('背景动效加载失败，页面内容仍可使用。');
    });
  });

  var topoState = 'idle';
  var topoButton = $('#topoLoad');
  var topoPlaceholder = $('#topoPlaceholder');
  var topoFeedback = $('#topoLoadStatus');
  var topoStage = $('.topo-stage');
  function loadTopo() {
    if (topoState !== 'idle') return;
    topoState = 'loading';
    if (topoButton) { topoButton.disabled = true; topoButton.textContent = '正在加载本地资源…'; }
    function failed() {
      topoState = 'idle';
      if (topoStage) topoStage.classList.remove('topo-active');
      if (topoButton) { topoButton.disabled = false; topoButton.textContent = '重新加载交互拓扑 ↗'; }
      if (topoFeedback) topoFeedback.textContent = '资源加载失败，仍可查看静态拓扑。';
    }
    function injectTopo() {
      root.classList.remove('no-webgl');
      if (topoFeedback) topoFeedback.textContent = '';
      if (topoStage) topoStage.classList.add('topo-active');
      loadScript('assets/js/topo.js', function () {
        if (root.classList.contains('no-webgl')) { failed(); return; }
        topoState = 'ready';
        if (topoPlaceholder) topoPlaceholder.hidden = true;
        if (topoButton) topoButton.disabled = false;
        var staticButton = $('#topoStatic');
        if (staticButton) { staticButton.hidden = false; staticButton.focus({ preventScroll: true }); }
        announce('交互拓扑已加载，可拖拽旋转。');
      }, failed);
    }
    if (window.THREE) injectTopo();
    else loadScript('vendor/three/build/three.min.js', injectTopo, failed);
  }
  if (topoButton) topoButton.addEventListener('click', function () {
    if (topoState === 'ready') {
      topoStage.classList.add('topo-active');
      topoPlaceholder.hidden = true;
      $('#topoStatic').hidden = false;
      $('#topoStatic').focus({ preventScroll: true });
      return;
    }
    loadTopo();
  });
  var topoStatic = $('#topoStatic');
  if (topoStatic) topoStatic.addEventListener('click', function () {
    topoStage.classList.remove('topo-active');
    topoPlaceholder.hidden = false;
    topoStatic.hidden = true;
    if (topoButton) {
      topoButton.textContent = '显示交互拓扑 ↗';
      topoButton.focus({ preventScroll: true });
    }
  });
})();
