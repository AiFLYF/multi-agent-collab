/* ============================================================
 * hero.js — 异构信号汇流（Canvas 2D）
 * 六个平台以不同运动签名从六路汇入「共享黑板」；
 * 人类指令 = 琥珀脉冲沿专属 lane 插入，到达后六流齐亮。
 *
 * 黑板位置不再由画布硬编码：每帧从 DOM 卡片 #boardCard 的
 * 实际 boundingRect 推导，因此桌面双栏 / 移动单栏都天然对齐，
 * 画布不会与页面文字重叠。无依赖，file:// 可跑。
 * ============================================================ */
(function () {
  'use strict';

  var canvas = document.getElementById('heroScene');
  if (!canvas) { document.documentElement.className += ' no-webgl'; window.__sceneBoot = 'partial'; return; }
  var ctx = canvas.getContext && canvas.getContext('2d');
  if (!ctx) { document.documentElement.className += ' no-webgl'; window.__sceneBoot = 'partial'; return; }

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var MONO = '"PlexMono",Consolas,"Microsoft YaHei",monospace';

  /* 六个信号源：呼号 / 平台 / 色 / 运动签名 */
  var PLATFORMS = [
    { code: 'CLD', name: 'Claude Code', color: '#FF8A50', style: 'rows' },
    { code: 'CDX', name: 'Codex CLI', color: '#37D67A', style: 'beam' },
    { code: 'OPC', name: 'OpenCode', color: '#53C8FF', style: 'wave' },
    { code: 'PI0', name: 'Pi', color: '#B79CFF', style: 'spiral' },
    { code: 'TR4', name: 'Trae IDE', color: '#6E9BFF', style: 'dash' },
    { code: 'WB0', name: 'WorkBuddy', color: '#D9F24A', style: 'hop' }
  ];
  var N_PER = 26;          /* 每路粒子数 */

  /* 画布与几何 */
  var W = 0, H = 0, DPR = 1;
  var sideIndexEl = document.querySelector('.side-index');
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = canvas.clientWidth; H = canvas.clientHeight;
    if (!W || !H) return;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    reserveRight = (sideIndexEl && getComputedStyle(sideIndexEl).display !== 'none') ? 168 : 20;
    computeOrigins();
  }

  /* ── 共享黑板：位置来自 DOM 卡片 ─────────────────────────── */
  var cardEl = document.getElementById('boardCard');
  function boardRect() {
    if (cardEl) {
      var r = cardEl.getBoundingClientRect();
      var c = canvas.getBoundingClientRect();
      if (r.width > 0) {
        return { x: r.left - c.left, y: r.top - c.top, w: r.width, h: r.height };
      }
    }
    return { x: W * 0.66 - 118, y: H * 0.5 - 75, w: 236, h: 150 };
  }
  function boardCenter() {
    var b = boardRect();
    return { x: b.x + b.w / 2, y: b.y + b.h / 2 };
  }

  /* ── 六路起点 ─────────────────────────────────────────────
   * 不绕圈（圆环必然有一段落在卡片背后），改为在卡片的「外接框」
   * 上取点：桌面 3 上 3 下，移动端 6 个全部来自画布底边。
   * 这样起点永远在卡片之外，lane 也不会横穿左侧文案。
   */
  var origins = [];
  var compact = false;                 /* 窄屏：只留流，不画点与标签 */
  function computeOrigins() {
    origins = [];
    if (!W || !H) return;
    var b = boardRect();
    var pad = 24;
    compact = W < 980 || b.x < W * 0.34;

    var x0 = Math.max(pad, b.x - 52);
    var x1 = Math.min(W - pad, b.x + b.w + 52);
    if (x1 - x0 < 120) { x0 = Math.max(pad, b.x - 20); x1 = Math.min(W - pad, b.x + b.w + 20); }
    var w = Math.max(1, x1 - x0);
    var xs = [x0 + w * 0.1, x0 + w * 0.5, x0 + w * 0.9];

    if (compact) {
      /* 窄屏：全部从底边升起，不用标签，绝不与文案打架 */
      for (var k = 0; k < 6; k++) {
        origins.push({ x: x0 + w * (0.06 + k * 0.176), y: H + 24, silent: true });
      }
      return;
    }
    var y0 = b.y - 52, y1 = b.y + b.h + 52;
    /* 上边三个、下边三个；顺序交错，避免同色相邻 */
    origins.push({ x: xs[0], y: y0, up: true });
    origins.push({ x: xs[2], y: y0, up: true });
    origins.push({ x: xs[1], y: y0, up: true });
    origins.push({ x: xs[1], y: y1, up: false });
    origins.push({ x: xs[0], y: y1, up: false });
    origins.push({ x: xs[2], y: y1, up: false });
  }

  /* 粒子：沿 lane 的进度 t∈[0,1]，0=起点 1=黑板 */
  var particles = [];
  function buildParticles() {
    particles = [];
    for (var i = 0; i < 6; i++) {
      for (var k = 0; k < N_PER; k++) {
        particles.push({
          p: i,
          t: Math.random(),
          speed: 0.055 + Math.random() * 0.075,
          phase: Math.random() * Math.PI * 2,
          size: 1.7 + Math.random() * 2.2
        });
      }
    }
  }

  /* 运动签名：返回粒子相对 lane 直线的偏移 {dx,dy} */
  function laneOffset(style, t, phase) {
    switch (style) {
      case 'rows': {                        /* 有序纵队：双排 + 步进 */
        var lane = Math.floor(t * 6) / 6;
        var side = (Math.floor(phase * 2) % 2 === 0) ? 7 : -7;
        return { dx: side * (1 - t * 0.5), dy: 0, qt: lane };
      }
      case 'beam':                          /* 高速细束：几乎直线 */
        return { dx: 0, dy: 0, trail: 0.5 + t * 0.5 };
      case 'wave':                          /* 正弦波 */
        return { dx: 0, dy: Math.sin(t * Math.PI * 2.2 + phase) * 26 * (1 - t * 0.6) };
      case 'spiral': {                      /* 螺旋 */
        var ang = phase + t * Math.PI * 3.2;
        var r = Math.sin(t * Math.PI) * 30;
        return { dx: Math.cos(ang) * r, dy: Math.sin(ang) * r };
      }
      case 'dash':                          /* 虚线 burst */
        return { dx: 0, dy: 0, gate: (t * 5) % 1 < 0.55 };
      case 'hop': {                         /* 环绕跳点 */
        var hop = Math.floor(t * 7) / 7 + 0.5 / 7;
        var a2 = phase + t * Math.PI * 5;
        var r2 = Math.sin(t * Math.PI) * 12;
        return { qt: hop, dx: Math.cos(a2) * r2, dy: Math.sin(a2) * r2 };
      }
      default:
        return { dx: 0, dy: 0 };
    }
  }

  /* ── 黑板锁定框（画布只画氛围，文字由 DOM 卡片承担） ─────── */
  function drawBoardFrame(time) {
    var b = boardRect();
    var cx = b.x + b.w / 2, cy = b.y + b.h / 2;
    var breathe = 0.5 + 0.5 * Math.sin(time * 0.9);
    var R = Math.max(b.w, b.h) * 0.92;

    var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R);
    g.addColorStop(0, 'rgba(70,224,255,' + (0.1 + 0.05 * breathe).toFixed(3) + ')');
    g.addColorStop(0.5, 'rgba(70,224,255,.035)');
    g.addColorStop(1, 'rgba(70,224,255,0)');
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, 6.284);
    ctx.fill();

    var m = 9, L = 18;
    ctx.save();
    ctx.strokeStyle = 'rgba(70,224,255,' + (0.4 + 0.35 * breathe).toFixed(3) + ')';
    ctx.lineWidth = 1.5;
    ctx.shadowColor = 'rgba(70,224,255,.7)';
    ctx.shadowBlur = 10;
    [[b.x - m, b.y - m, 1, 1], [b.x + b.w + m, b.y - m, -1, 1],
     [b.x - m, b.y + b.h + m, 1, -1], [b.x + b.w + m, b.y + b.h + m, -1, -1]]
      .forEach(function (c) {
        ctx.beginPath();
        ctx.moveTo(c[0] + c[2] * L, c[1]);
        ctx.lineTo(c[0], c[1]);
        ctx.lineTo(c[0], c[1] + c[3] * L);
        ctx.stroke();
      });
    ctx.restore();
  }

  /* ── 人类指令：琥珀脉冲 ──────────────────────────────────── */
  var dir = { active: false, t: 0, next: 3.5 };
  var ripples = [];
  var boost = 0;                 /* 指令广播：六流齐亮 */
  var logLine = function () {};  /* 由下方控制台日志实现覆盖 */

  function fireDirective() {
    if (dir.active) return;
    dir.active = true;
    dir.t = 0;
    logLine('hu', '▲ HUMAN ▸ 指令已下发 · 优先级最高');
  }
  function updateDirective(dt) {
    var c = boardCenter();
    if (dir.active) {
      dir.t += dt / 1.6;
      if (dir.t >= 1) {
        dir.active = false;
        dir.t = 0;
        dir.next = 7 + Math.random() * 4;
        boost = 1;
        ripples.push({ r: 10, a: 0.8 });
        flashRows();
        logLine('hu', '▲ HUMAN ▸ 已抵达共享黑板 · 六路广播中');
      }
    } else {
      dir.next -= dt;
      if (dir.next <= 0) fireDirective();
    }
    var p = null;
    if (dir.active) {
      var k = dir.t, e = k * k * (3 - 2 * k);
      /* 桌面从顶部下行；窄屏从左侧进入，避免横穿标题与正文 */
      var sx = compact ? -30 : c.x, sy = compact ? c.y : -30;
      p = { x: sx + (c.x - sx) * e, y: sy + (c.y - sy) * e };
    }
    for (var i = ripples.length - 1; i >= 0; i--) {
      var rp = ripples[i];
      rp.r += dt * 320;
      rp.a -= dt * 0.9;
      if (rp.a <= 0) ripples.splice(i, 1);
    }
    boost = Math.max(0, boost - dt * 0.55);
    return p;
  }
  function drawDirectivePacket(p) {
    if (!p) return;
    ctx.save();
    ctx.shadowColor = 'rgba(255,184,77,.9)';
    ctx.shadowBlur = 18;
    ctx.fillStyle = '#FFB84D';
    ctx.beginPath();
    ctx.arc(p.x, p.y, 5, 0, 6.284);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.font = '500 10px ' + MONO;
    ctx.fillStyle = '#FFB84D';
    ctx.textAlign = 'center';
    ctx.fillText('HUMAN DIRECTIVE · 人类指令', p.x, p.y - 14);
    ctx.restore();
  }
  function drawRipples() {
    var c = boardCenter();
    ripples.forEach(function (rp) {
      ctx.save();
      ctx.strokeStyle = 'rgba(255,184,77,' + rp.a.toFixed(3) + ')';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(c.x, c.y, rp.r, 0, 6.284);
      ctx.stroke();
      ctx.restore();
    });
  }
  canvas.addEventListener('pointerdown', fireDirective);

  /* ── DOM 卡片行点亮（画布与页面真实联动） ────────────────── */
  var rows = [];
  var rowIdx = 0;
  var flashCool = 0;
  function flashRows() {
    rows.forEach(function (r) { r.classList.add('flash'); });
    setTimeout(function () { rows.forEach(function (r) { r.classList.remove('flash'); }); }, 620);
  }
  function flashOne(time) {
    if (!rows.length || time - flashCool < 0.85) return;   /* 节流：别闪成迪厅 */
    flashCool = time;
    var r = rows[rowIdx % rows.length];
    rowIdx++;
    r.classList.add('flash');
    setTimeout(function () { r.classList.remove('flash'); }, 520);
  }

  /* ── lane 路线（虚线）+ 起点呼号标签 ─────────────────────── */
  var reserveRight = 20;                 /* 给右侧章节索引留出的空间 */
  function drawLanes() {
    var c = boardCenter();
    ctx.save();
    for (var i = 0; i < 6; i++) {
      var o = origins[i];
      if (!o) continue;
      ctx.strokeStyle = o.silent ? 'rgba(70,224,255,.05)' : 'rgba(70,224,255,.07)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 7]);
      ctx.beginPath();
      ctx.moveTo(o.x, o.y);
      ctx.lineTo(c.x, c.y);
      ctx.stroke();
      ctx.setLineDash([]);
      if (o.silent) continue;                 /* 窄屏不画点与标签 */

      var pf = PLATFORMS[i];
      ctx.fillStyle = pf.color;
      ctx.shadowColor = pf.color;
      ctx.shadowBlur = 10;
      ctx.beginPath();
      ctx.arc(o.x, o.y, 3.5, 0, 6.284);
      ctx.fill();
      ctx.shadowBlur = 0;

      var text = pf.code + ' · ' + pf.name;
      ctx.font = '500 10px ' + MONO;
      var half = ctx.measureText(text).width / 2;
      var lx = Math.max(half + 14, Math.min(W - reserveRight - half, o.x));
      ctx.fillStyle = pf.color;
      ctx.textAlign = 'center';
      ctx.fillText(text, lx, o.y + (o.up ? -14 : 18));
    }
    ctx.restore();
  }

  /* ── 粒子推进与绘制 ──────────────────────────────────────── */
  function drawParticles(time, dt) {
    var c = boardCenter();
    var bMul = 1 + boost * 0.9;
    ctx.save();
    for (var i = 0; i < particles.length; i++) {
      var pt = particles[i];
      var pf = PLATFORMS[pt.p];
      var o = origins[pt.p];
      if (!o) continue;
      pt.t += pt.speed * dt * (1 + boost * 0.6);
      if (pt.t >= 1) {
        pt.t = 0;
        pt.phase = Math.random() * Math.PI * 2;
        flashOne(time);
      }
      var off = laneOffset(pf.style, pt.t, pt.phase);
      if (off.gate === false) continue;                    /* dash：空隙不画 */
      var tt = (off.qt !== undefined) ? off.qt : pt.t;
      var ease = tt * tt * (3 - 2 * tt);
      var x = o.x + (c.x - o.x) * ease + (off.dx || 0);
      var y = o.y + (c.y - o.y) * ease + (off.dy || 0);
      var near = 0.35 + 0.65 * ease;
      var alpha = near * bMul * (0.5 + 0.5 * Math.sin(time * 2 + pt.phase));
      if (alpha <= 0.02) continue;
      var size = pt.size * (0.7 + ease * 1.6) * bMul;
      ctx.globalAlpha = Math.min(1, alpha);
      if (pf.style === 'beam' && off.trail) {
        var px = o.x + (c.x - o.x) * Math.max(0, ease - 0.12);
        var py = o.y + (c.y - o.y) * Math.max(0, ease - 0.12);
        ctx.strokeStyle = pf.color;
        ctx.lineWidth = size * 0.8;
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(x, y);
        ctx.stroke();
      }
      if (pf.style === 'rows') {
        ctx.fillStyle = pf.color;
        ctx.fillRect(x - size, y - size, size * 2, size * 2);
      } else {
        ctx.fillStyle = pf.color;
        ctx.shadowColor = pf.color;
        ctx.shadowBlur = size * 2.2;
        ctx.beginPath();
        ctx.arc(x, y, size, 0, 6.284);
        ctx.fill();
        ctx.shadowBlur = 0;
      }
    }
    ctx.restore();
  }

  /* ── 背景：同心环 + 方位刻度 ─────────────────────────────── */
  function drawField() {
    var c = boardCenter();
    ctx.save();
    var rMax = Math.max(W, H) * 0.75;
    for (var i = 1; i <= 4; i++) {
      ctx.strokeStyle = 'rgba(70,224,255,' + (0.05 - i * 0.006) + ')';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(c.x, c.y, (rMax / 4) * i, 0, 6.284);
      ctx.stroke();
    }
    ctx.strokeStyle = 'rgba(70,224,255,.045)';
    for (var a = 0; a < 12; a++) {
      var ang = a * Math.PI / 6;
      ctx.beginPath();
      ctx.moveTo(c.x + Math.cos(ang) * (rMax / 4) * 3.6, c.y + Math.sin(ang) * (rMax / 4) * 3.6);
      ctx.lineTo(c.x + Math.cos(ang) * (rMax / 4) * 4, c.y + Math.sin(ang) * (rMax / 4) * 4);
      ctx.stroke();
    }
    ctx.restore();
  }

  /* ── 控制台日志（DOM，hero 左下） ────────────────────────── */
  var logBox = document.getElementById('heroLog');
  var LOG_POOL = [
    ['ok', 'WB0-261012-2055 ▸ 心跳 2m · feature/landing-page'],
    ['ok', 'CDX-261012-1812 ▸ 读规则 · AGENTS.md §3'],
    ['ok', 'CLD-261012-0910 ▸ 写会话 · sessions/claude-0910.md'],
    ['ok', 'PI0-261011-2230 ▸ 读留言 · Q-2026-10-12-02 已答'],
    ['ok', 'TR4-261012-0405 ▸ 双写忽略 · worktree feature/coupon'],
    ['hu', '▲ HUMAN ▸ 先停一下，别碰登录模块'],
    ['ok', 'OPC-261012-1245 ▸ 接管声明 · 等 5 分钟无反对'],
    ['ok', 'CLD-261012-0910 ▸ 开 PR · feature/order-api ▸ develop']
  ];
  var logIdx = Math.floor(Math.random() * LOG_POOL.length);
  logLine = function (cls, text) {
    if (!logBox) return;
    var d = document.createElement('div');
    var now = new Date();
    var t = ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2) + ':' + ('0' + now.getSeconds()).slice(-2);
    d.innerHTML = '<span class="t">' + t + '</span>  <span class="' + cls + '">' + text + '</span>';
    logBox.appendChild(d);
    while (logBox.children.length > 3) logBox.removeChild(logBox.firstChild);
    requestAnimationFrame(function () { d.classList.add('show'); });
  };
  setInterval(function () {
    var e = LOG_POOL[logIdx % LOG_POOL.length];
    logIdx++;
    if (e[0] !== 'hu') logLine(e[0], e[1]);
  }, 2600);

  /* ── 主循环 ──────────────────────────────────────────────── */
  var last = performance.now();
  var time = 0;
  function frame(now) {
    var dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    time += dt;
    var p = updateDirective(dt);
    ctx.clearRect(0, 0, W, H);
    drawField();
    drawLanes();
    drawParticles(time, dt);
    drawBoardFrame(time);
    drawRipples();
    drawDirectivePacket(p);
  }

  var running = true;
  function loop(now) {
    requestAnimationFrame(loop);
    if (!running) { last = performance.now(); return; }
    frame(now || performance.now());
  }

  function boot() {
    rows = Array.prototype.slice.call(document.querySelectorAll('.bc-row'));
    resize();
    buildParticles();
    if (reduceMotion) {
      time = 0.5;
      frame(performance.now());
    } else {
      requestAnimationFrame(loop);
    }
    window.__sceneBoot = 'ready';
  }

  window.addEventListener('resize', function () { resize(); });
  document.addEventListener('visibilitychange', function () { running = !document.hidden; });
  if ('IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) { running = es[0].isIntersecting && !document.hidden; }, { threshold: 0.02 });
    io.observe(canvas);
  }
  /* 卡片尺寸随字体/断点变化，尺寸稳定后再算一次几何 */
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () { resize(); });
  }
  window.addEventListener('load', function () { resize(); });
  boot();
})();
