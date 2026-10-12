/* ============================================================
 * hero.js — 异构信号汇流（Canvas 2D）
 * 六个平台以不同运动签名从六路汇入「共享黑板」；
 * 人类指令 = 琥珀脉冲沿专属 lane 插入，到达后六流齐亮。
 * 无依赖，file:// 可跑。
 * ============================================================ */
(function () {
  'use strict';

  var canvas = document.getElementById('heroScene');
  if (!canvas) { document.documentElement.className += ' no-webgl'; window.__sceneBoot = 'partial'; return; }
  var ctx = canvas.getContext && canvas.getContext('2d');
  if (!ctx) { document.documentElement.className += ' no-webgl'; window.__sceneBoot = 'partial'; return; }

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* 六个信号源：呼号 / 平台 / 色 / 运动签名 */
  var PLATFORMS = [
    { code: 'CLD', name: 'Claude Code', color: '#FF8A50', style: 'rows' },
    { code: 'CDX', name: 'Codex CLI', color: '#37D67A', style: 'beam' },
    { code: 'OPC', name: 'OpenCode', color: '#53C8FF', style: 'wave' },
    { code: 'PI0', name: 'Pi', color: '#B79CFF', style: 'spiral' },
    { code: 'TR4', name: 'Trae IDE', color: '#6E9BFF', style: 'dash' },
    { code: 'WB0', name: 'WorkBuddy', color: '#D9F24A', style: 'hop' }
  ];
  var N_PER = 30;          /* 每路粒子数 */
  var DPR = 1;

  /* 画布与几何 */
  var W = 0, H = 0, CX = 0, CY = 0;
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = canvas.clientWidth; H = canvas.clientHeight;
    if (!W || !H) return;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    CX = W * 0.68;          /* 黑板偏右，左侧留给文案 */
    CY = H * 0.5;
    computeOrigins();
  }

  /* 六路起点：绕黑板的一圈（顶部起，顺时针） */
  var origins = [];
  function computeOrigins() {
    origins = [];
    var rx = Math.min(W * 0.3, 520), ry = Math.min(H * 0.4, 330);
    for (var i = 0; i < 6; i++) {
      var a = -Math.PI / 2 + i * Math.PI / 3;
      origins.push({ x: CX + Math.cos(a) * rx, y: CY + Math.sin(a) * ry });
    }
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

  /* 运动签名：返回粒子相对 lane 直线的偏移 {dx,dy} 与缩放 */
  function laneOffset(style, t, phase, time) {
    switch (style) {
      case 'rows': {                       /* 有序纵队：双排 + 步进 */
        var lane = Math.floor(t * 6) / 6;  // 步进感
        var side = (Math.floor(phase * 2) % 2 === 0) ? 7 : -7;
        return { dx: side * (1 - t * 0.5), dy: 0, qt: lane };
      }
      case 'beam':                          /* 高速细束：几乎直线，密度低 */
        return { dx: 0, dy: 0, trail: 0.5 + t * 0.5 };
      case 'wave':                          /* 正弦波 */
        return { dx: 0, dy: Math.sin(t * Math.PI * 2.2 + phase) * 26 * (1 - t * 0.6) };
      case 'spiral':                        /* 螺旋 */
        var ang = phase + t * Math.PI * 3.2;
        var r = Math.sin(t * Math.PI) * 30;
        return { dx: Math.cos(ang) * r, dy: Math.sin(ang) * r };
      case 'dash':                          /* 虚线 burst */
        return { dx: 0, dy: 0, gate: (t * 5) % 1 < 0.55 };
      case 'hop':                           /* 环绕跳点 */
        var hop = Math.floor(t * 7) / 7 + 0.5 / 7;
        var a2 = phase + t * Math.PI * 5;
        var r2 = Math.sin(t * Math.PI) * 12;
        return { qt: hop, dx: Math.cos(a2) * r2, dy: Math.sin(a2) * r2 };
      default:
        return { dx: 0, dy: 0 };
    }
  }
  /* ── 共享黑板（中央） ────────────────────────────────────── */
  var BOARD_W = 236, BOARD_H = 150;
  var STICKERS = [
    { label: 'sessions', flash: 0 },
    { label: 'worklog', flash: 0 },
    { label: 'inbox', flash: 0 }
  ];
  function boardRect() {
    return { x: CX - BOARD_W / 2, y: CY - BOARD_H / 2, w: BOARD_W, h: BOARD_H };
  }
  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }
  function drawBoard(time) {
    var b = boardRect();
    var breathe = 0.5 + 0.5 * Math.sin(time * 0.9);
    ctx.save();
    ctx.shadowColor = 'rgba(70,224,255,' + (0.35 + 0.25 * breathe) + ')';
    ctx.shadowBlur = 26 + breathe * 14;
    ctx.fillStyle = 'rgba(7,13,20,.94)';
    roundRect(ctx, b.x, b.y, b.w, b.h, 10);
    ctx.fill();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = 'rgba(70,224,255,.55)';
    ctx.lineWidth = 1;
    ctx.stroke();
    /* 标题 */
    ctx.font = '600 12px ' + MONO;
    ctx.fillStyle = '#46E0FF';
    ctx.textAlign = 'left';
    ctx.fillText('.agents/  共享黑板', b.x + 14, b.y + 24);
    /* 横格线 */
    ctx.strokeStyle = 'rgba(70,224,255,.1)';
    for (var i = 1; i <= 3; i++) {
      ctx.beginPath();
      ctx.moveTo(b.x + 12, b.y + 34 + i * 18);
      ctx.lineTo(b.x + b.w - 12, b.y + 34 + i * 18);
      ctx.stroke();
    }
    /* 三张便签：sessions / worklog / inbox */
    var sw = (b.w - 28 - 16) / 3;
    for (var s = 0; s < 3; s++) {
      var sx = b.x + 14 + s * (sw + 8);
      var sy = b.y + b.h - 40;
      var f = STICKERS[s].flash;
      ctx.fillStyle = f > 0 ? 'rgba(70,224,255,' + (0.1 + 0.5 * f) + ')' : 'rgba(70,224,255,.06)';
      roundRect(ctx, sx, sy, sw, 26, 5);
      ctx.fill();
      ctx.strokeStyle = f > 0 ? 'rgba(70,224,255,' + (0.4 + 0.6 * f) + ')' : 'rgba(70,224,255,.18)';
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(sx + 10, sy + 13, 3, 0, 6.284);
      ctx.fillStyle = f > 0 ? '#46E0FF' : 'rgba(70,224,255,.35)';
      ctx.fill();
      ctx.font = '500 10px ' + MONO;
      ctx.fillStyle = f > 0 ? '#DFF1FA' : 'rgba(143,169,188,.8)';
      ctx.fillText(STICKERS[s].label, sx + 19, sy + 17);
    }
    ctx.restore();
  }

  /* ── 人类指令：琥珀脉冲 ──────────────────────────────────── */
  var dir = { active: false, t: 0, next: 3.5 };
  var ripples = [];
  var boost = 0;                 /* 指令广播：六流齐亮 */
  function fireDirective() {
    if (dir.active) return;
    dir.active = true;
    dir.t = 0;
    logLine('hu', '▲ HUMAN ▸ 指令已下发 · 优先级最高');
  }
  function updateDirective(dt, time) {
    if (dir.active) {
      dir.t += dt / 1.6;
      if (dir.t >= 1) {
        dir.active = false;
        dir.t = 0;
        dir.next = 7 + Math.random() * 4;
        boost = 1;
        ripples.push({ r: 10, a: 0.8 });
        STICKERS.forEach(function (s) { s.flash = 1; });
        logLine('hu', '▲ HUMAN ▸ 已抵达共享黑板 · 六路广播中');
      }
    } else {
      dir.next -= dt;
      if (dir.next <= 0) fireDirective();
    }
    /* 指令粒子位置（从顶部沿专属 lane 下行） */
    var p = null;
    if (dir.active) {
      var k = dir.t;
      var sx = CX, sy = -30;
      p = { x: sx + (CX - sx) * k, y: sy + (CY - 20 - sy) * (k * k * (3 - 2 * k)) };
    }
    /* 涟漪 */
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
    ripples.forEach(function (rp) {
      ctx.save();
      ctx.strokeStyle = 'rgba(255,184,77,' + rp.a.toFixed(3) + ')';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(CX, CY, rp.r, 0, 6.284);
      ctx.stroke();
      ctx.restore();
    });
  }
  canvas.addEventListener('pointerdown', fireDirective);
  var MONO = '"PlexMono",Consolas,"Microsoft YaHei",monospace';

  /* ── lane 路线（虚线）+ 起点呼号标签 ─────────────────────── */
  function drawLanes() {
    ctx.save();
    for (var i = 0; i < 6; i++) {
      var o = origins[i];
      ctx.strokeStyle = 'rgba(70,224,255,.08)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 7]);
      ctx.beginPath();
      ctx.moveTo(o.x, o.y);
      ctx.lineTo(CX, CY);
      ctx.stroke();
      ctx.setLineDash([]);
      /* 起点：色点 + 呼号 + 平台名 */
      var pf = PLATFORMS[i];
      ctx.fillStyle = pf.color;
      ctx.beginPath();
      ctx.arc(o.x, o.y, 3.5, 0, 6.284);
      ctx.fill();
      ctx.shadowColor = pf.color;
      ctx.shadowBlur = 10;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.font = '500 10px ' + MONO;
      ctx.fillStyle = pf.color;
      ctx.textAlign = 'center';
      ctx.fillText(pf.code + ' · ' + pf.name, o.x, o.y - 14);
    }
    ctx.restore();
  }

  /* ── 粒子推进与绘制 ──────────────────────────────────────── */
  function drawParticles(time, dt) {
    var bMul = 1 + boost * 0.9;
    ctx.save();
    for (var i = 0; i < particles.length; i++) {
      var pt = particles[i];
      var pf = PLATFORMS[pt.p];
      var o = origins[pt.p];
      pt.t += pt.speed * dt * (1 + boost * 0.6);
      if (pt.t >= 1) {
        pt.t = 0;
        pt.phase = Math.random() * Math.PI * 2;
        /* 抵达黑板：随机点亮一张便签 */
        STICKERS[Math.floor(Math.random() * 3)].flash = 1;
      }
      var off = laneOffset(pf.style, pt.t, pt.phase, time);
      if (off.gate === false) continue;                    /* dash：空隙不画 */
      var tt = (off.qt !== undefined) ? off.qt : pt.t;
      var ease = tt * tt * (3 - 2 * tt);                    /* smoothstep 起点慢、终点快 */
      var x = o.x + (CX - o.x) * ease + (off.dx || 0);
      var y = o.y + (CY - o.y) * ease + (off.dy || 0);
      /* 朝黑板方向越近越亮越大 */
      var near = 0.35 + 0.65 * ease;
      var alpha = near * bMul * (0.5 + 0.5 * Math.sin(time * 2 + pt.phase));
      if (alpha <= 0.02) continue;
      var size = pt.size * (0.7 + ease * 1.6) * bMul;
      ctx.globalAlpha = Math.min(1, alpha);
      if (pf.style === 'beam' && off.trail) {
        /* 彗尾 */
        var px = o.x + (CX - o.x) * Math.max(0, ease - 0.12) ;
        var py = o.y + (CY - o.y) * Math.max(0, ease - 0.12);
        ctx.strokeStyle = pf.color;
        ctx.lineWidth = size * 0.8;
        ctx.beginPath();
        ctx.moveTo(px, py);
        ctx.lineTo(x, y);
        ctx.stroke();
      }
      if (pf.style === 'rows') {
        ctx.fillStyle = pf.color;                            /* 方块：纵队感 */
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
    ctx.save();
    var rMax = Math.max(W, H) * 0.75;
    for (var i = 1; i <= 4; i++) {
      ctx.strokeStyle = 'rgba(70,224,255,' + (0.05 - i * 0.006) + ')';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(CX, CY, (rMax / 4) * i, 0, 6.284);
      ctx.stroke();
    }
    ctx.strokeStyle = 'rgba(70,224,255,.045)';
    for (var a = 0; a < 12; a++) {
      var ang = a * Math.PI / 6;
      ctx.beginPath();
      ctx.moveTo(CX + Math.cos(ang) * (rMax / 4) * 3.6, CY + Math.sin(ang) * (rMax / 4) * 3.6);
      ctx.lineTo(CX + Math.cos(ang) * (rMax / 4) * 4, CY + Math.sin(ang) * (rMax / 4) * 4);
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
  function logLine(cls, text) {
    if (!logBox) return;
    var d = document.createElement('div');
    var now = new Date();
    var t = ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2) + ':' + ('0' + now.getSeconds()).slice(-2);
    d.innerHTML = '<span class="t">' + t + '</span>  <span class="' + cls + '">' + text + '</span>';
    logBox.appendChild(d);
    while (logBox.children.length > 3) logBox.removeChild(logBox.firstChild);
    requestAnimationFrame(function () { d.classList.add('show'); });
  }
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
    STICKERS.forEach(function (s) { s.flash = Math.max(0, s.flash - dt * 1.4); });
    var p = updateDirective(dt, time);
    ctx.clearRect(0, 0, W, H);
    drawField();
    drawLanes();
    drawParticles(time, dt);
    drawBoard(time);
    drawRipples();
    drawDirectivePacket(p);
  }
  function loop(now) {
    requestAnimationFrame(loop);
    frame(now || performance.now());
  }

  window.addEventListener('resize', resize);
  resize();
  buildParticles();
  if (reduceMotion) {
    /* 减少动效：渲染一帧定格 */
    time = 0.5;
    frame(performance.now());
  } else {
    requestAnimationFrame(loop);
  }
  window.__sceneBoot = 'ready';
})();
