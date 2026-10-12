/* ============================================================
 * topo.js — 第 05 章拓扑图：六平台节点 ──▶ 共享黑板 ◀── 人类
 *           TubeGeometry 发光管道 + 沿线流动数据包 + 拖拽旋转
 *
 * 文字标签不再用精灵贴图（会随旋转互相压叠、且缩放后发虚），
 * 改为 HTML 覆盖层：每帧把锚点投影到屏幕坐标，再做竖向去重叠，
 * 保证任何视角下标签都清晰、互不遮挡。
 * three.js r149 UMD，与 hero.js 各自独立渲染器。
 * ============================================================ */
(function () {
  'use strict';

  function fail() { document.documentElement.className += ' no-webgl'; }
  if (typeof THREE === 'undefined') { fail(); return; }
  var canvas = document.getElementById('topoScene');
  var labelsBox = document.getElementById('topoLabels');
  if (!canvas) { fail(); return; }

  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
  } catch (e) { fail(); return; }

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
  renderer.setClearColor(0x000000, 0);

  var PLATFORMS = [
    { name: 'Claude Code', color: 0xFF6B35 },
    { name: 'Codex CLI', color: 0x10A37F },
    { name: 'OpenCode', color: 0x35D9FF },
    { name: 'Pi', color: 0xA78BFF },
    { name: 'Trae IDE', color: 0x4E8FFF },
    { name: 'WorkBuddy', color: 0xC6FF4A }
  ];

  /* 场景尺度：横向椭圆环，让构图在宽屏下也铺得开 */
  var RING_RX = 220, RING_RZ = 115, NODE_Y = 36;
  var SCENE_CY = 60;                    /* 取景中心（世界 Y） */
  var HUMAN_POS = new THREE.Vector3(0, 148, -24);

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(45, 1, 1, 4000);
  var root = new THREE.Group();
  scene.add(root);

  /* ── 贴图工具 ────────────────────────────────────────────── */
  function glowTexture() {
    var c = document.createElement('canvas');
    c.width = c.height = 128;
    var g = c.getContext('2d');
    var grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.22, 'rgba(255,255,255,0.6)');
    grad.addColorStop(0.55, 'rgba(255,255,255,0.14)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  }
  var glowTex = glowTexture();

  function glowSprite(color, size, opacity) {
    var sp = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowTex, color: color, transparent: true, opacity: opacity,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    sp.scale.set(size, size, 1);
    return sp;
  }

  /* ── HTML 标签层 ─────────────────────────────────────────── */
  var LABELS = [];
  function makeLabel(text, sub, colorHex, kind, gap) {
    var el = document.createElement('div');
    el.className = 'topo-label' + (kind ? ' is-' + kind : '');
    var dot = document.createElement('i');
    var col = '#' + ('000000' + colorHex.toString(16)).slice(-6);
    dot.style.background = col;
    dot.style.boxShadow = '0 0 10px ' + col;
    el.appendChild(dot);
    el.appendChild(document.createTextNode(text));
    if (sub) {
      var u = document.createElement('u');
      u.textContent = sub;
      el.appendChild(u);
    }
    if (labelsBox) labelsBox.appendChild(el);
    var item = { el: el, pos: new THREE.Vector3(), gap: gap || 26, w: 0 };
    LABELS.push(item);
    return item;
  }

  /* ── 中心：共享黑板 ──────────────────────────────────────── */
  var center = new THREE.Group();
  center.add(new THREE.Mesh(
    new THREE.SphereGeometry(24, 48, 48),
    new THREE.MeshBasicMaterial({ color: 0x0d1620 })
  ));
  center.add(new THREE.LineSegments(
    new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(33, 1)),
    new THREE.LineBasicMaterial({ color: 0x35D9FF, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false })
  ));
  center.add(glowSprite(0x9be8ff, 132, 0.45));
  var bbRing = new THREE.Mesh(
    new THREE.RingGeometry(0.975, 1.0, 128),
    new THREE.MeshBasicMaterial({ color: 0x35D9FF, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide })
  );
  bbRing.scale.set(46, 46, 1);
  center.add(bbRing);
  root.add(center);
  makeLabel('共享黑板 .agents/', '谁在干嘛 · 心跳 · 留言', 0xFFFFFF, 'center', 30)
    .pos.set(0, -58, 0);

  /* ── 平台节点 + 人类节点 + 管道 + 数据包 ─────────────────── */
  var packets = [];
  function addTube(from, mid, to, color, opacity) {
    var curve = new THREE.QuadraticBezierCurve3(from, mid, to);
    root.add(new THREE.Mesh(
      new THREE.TubeGeometry(curve, 48, 1.3, 8, false),
      new THREE.MeshBasicMaterial({ color: color, transparent: true, opacity: opacity, blending: THREE.AdditiveBlending, depthWrite: false })
    ));
    root.add(new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(curve.getPoints(64)),
      new THREE.LineBasicMaterial({ color: color, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending, depthWrite: false })
    ));
    for (var i = 0; i < 2; i++) {
      packets.push({
        curve: curve,
        t: i * 0.5 + Math.random() * 0.2,
        speed: 0.1 + Math.random() * 0.08,
        sprite: (function () {
          var sp = glowSprite(color, 9, 0.9);
          root.add(sp);
          return sp;
        })()
      });
    }
  }

  PLATFORMS.forEach(function (pf, i) {
    /* 从 0° 起排：六个节点都错开 z 轴，避免有节点正对/正背镜头、
       标签投影到中心球体上 */
    var ang = i * Math.PI / 3;
    var px = Math.cos(ang) * RING_RX;
    var pz = Math.sin(ang) * RING_RZ;
    var py = Math.sin(i * 1.7) * NODE_Y;

    var node = new THREE.Group();
    node.position.set(px, py, pz);
    node.add(new THREE.Mesh(
      new THREE.SphereGeometry(9, 32, 32),
      new THREE.MeshBasicMaterial({ color: pf.color })
    ));
    node.add(glowSprite(pf.color, 56, 0.6));
    root.add(node);

    makeLabel(pf.name, 'AGENTS.md', pf.color, '', 24)
      .pos.set(px, py + 30, pz);

    var from = new THREE.Vector3(px, py, pz);
    var to = new THREE.Vector3(0, 0, 0);
    var mid = from.clone().lerp(to, 0.5);
    mid.y += 66;
    addTube(from, mid, to, pf.color, 0.22);
  });

  /* 人类节点（琥珀色，从上方连入黑板） */
  var human = new THREE.Group();
  human.position.copy(HUMAN_POS);
  human.add(new THREE.Mesh(
    new THREE.SphereGeometry(11, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xFFB020 })
  ));
  human.add(glowSprite(0xFFB020, 62, 0.65));
  root.add(human);
  makeLabel('你 · 人类指令', '优先级最高', 0xFFB020, 'human', 30)
    .pos.set(HUMAN_POS.x, HUMAN_POS.y + 28, HUMAN_POS.z);
  addTube(HUMAN_POS, new THREE.Vector3(0, 84, -16), new THREE.Vector3(0, 0, 0), 0xFFB020, 0.22);

  /* ── 取景：按舞台宽高比自适应，图形始终铺满 ─────────────── */
  var SCENE_HALF_H = 136, SCENE_HALF_W = 258, FIT_MARGIN = 1.03;
  function fitCamera() {
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    var half = Math.tan(camera.fov * Math.PI / 360);
    var dH = (SCENE_HALF_H * FIT_MARGIN) / half;
    var dW = (SCENE_HALF_W * FIT_MARGIN) / (half * camera.aspect);
    var d = Math.max(dH, dW);
    camera.position.set(0, SCENE_CY, d);
    camera.lookAt(0, SCENE_CY, 0);
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  }

  /* ── 标签投影 + 竖向去重叠 ───────────────────────────────── */
  var tmpV = new THREE.Vector3();
  var slots = [];
  function measureLabels() {
    LABELS.forEach(function (L) { L.w = L.el.offsetWidth || 90; });
  }
  function layoutLabels() {
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    slots.length = 0;
    for (var i = 0; i < LABELS.length; i++) {
      var L = LABELS[i];
      tmpV.copy(L.pos);
      root.localToWorld(tmpV);
      tmpV.project(camera);
      var pad = 14;
      var x = (tmpV.x * 0.5 + 0.5) * w;
      var y = (-tmpV.y * 0.5 + 0.5) * h;
      x = Math.max(pad + L.w / 2, Math.min(w - pad - L.w / 2, x));
      slots.push({ el: L.el, x: x, y: y, gap: L.gap });
    }
    slots.sort(function (a, b) { return a.y - b.y; });

    var pad = 16;
    for (var pass = 0; pass < 3; pass++) {
      for (var k = 1; k < slots.length; k++) {
        var need = slots[k - 1].y + (slots[k - 1].gap + slots[k].gap) / 2;
        if (slots[k].y < need) slots[k].y = need;
      }
      var last = slots[slots.length - 1];
      var over = last.y + last.gap / 2 - (h - pad);
      if (over > 0) {
        for (var m = slots.length - 1; m >= 0; m--) {
          slots[m].y -= over;
          if (m > 0 && slots[m].y - slots[m - 1].y >= (slots[m - 1].gap + slots[m].gap) / 2) break;
        }
      }
      if (slots[0].y - slots[0].gap / 2 < pad) {
        var up = pad - (slots[0].y - slots[0].gap / 2);
        for (var n = 0; n < slots.length; n++) {
          slots[n].y += up;
          if (n < slots.length - 1 && slots[n + 1].y - slots[n].y >= (slots[n].gap + slots[n + 1].gap) / 2) break;
        }
      }
    }
    for (var q = 0; q < slots.length; q++) {
      var s = slots[q];
      s.el.style.transform = 'translate(' + s.x.toFixed(1) + 'px,' + s.y.toFixed(1) + 'px) translate(-50%,-50%)';
    }
  }

  /* ── 交互：拖拽旋转 ──────────────────────────────────────── */
  var targetRY = 0, curRY = 0, targetRX = 0, curRX = 0;
  var dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener('pointerdown', function (e) {
    dragging = true;
    lastX = e.clientX; lastY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    targetRY += (e.clientX - lastX) * 0.0055;
    targetRX = Math.max(-0.45, Math.min(0.45, targetRX + (e.clientY - lastY) * 0.003));
    lastX = e.clientX; lastY = e.clientY;
  });
  function endDrag() { dragging = false; }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('pointerleave', endDrag);

  /* ── 主循环 ──────────────────────────────────────────────── */
  var running = true, visible = true;
  document.addEventListener('visibilitychange', function () { running = !document.hidden; });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (es) {
      visible = es[0].isIntersecting;
      if (visible && !running) running = true;
    }, { threshold: 0.05 }).observe(canvas);
  }

  function frame(dt) {
    curRY += (targetRY - curRY) * 0.08;
    curRX += (targetRX - curRX) * 0.08;
    if (!dragging && !reduceMotion) targetRY += dt * 0.12;
    root.rotation.y = curRY;
    root.rotation.x = curRX;
    bbRing.quaternion.copy(camera.quaternion);
    for (var i = 0; i < packets.length; i++) {
      var p = packets[i];
      p.t = (p.t + dt * p.speed) % 1;
      p.sprite.position.copy(p.curve.getPointAt(p.t));
      var f = Math.sin(p.t * Math.PI);
      p.sprite.material.opacity = 0.25 + 0.7 * f;
      var s = 7 + 7 * f;
      p.sprite.scale.set(s, s, 1);
    }
    root.updateMatrixWorld();
    layoutLabels();
    renderer.render(scene, camera);
  }

  function onResize() { fitCamera(); measureLabels(); }
  window.addEventListener('resize', onResize);
  onResize();
  /* 字体就绪后标签宽度才准确，重量一次 */
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(function () { onResize(); });
  }

  if (reduceMotion) {
    frame(0.016);
    LABELS.forEach(function (L) { L.el.classList.add('in'); });
  } else {
    var last = performance.now();
    (function loop() {
      requestAnimationFrame(loop);
      if (!running || !visible) { last = performance.now(); return; }
      var now = performance.now();
      var dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      frame(dt);
    })();
    setTimeout(function () { LABELS.forEach(function (L) { L.el.classList.add('in'); }); }, 260);
  }
})();
