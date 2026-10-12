/* ============================================================
 * topo.js — 第 05 章拓扑图：六平台节点 ──▶ 共享黑板 ◀── 人类
 *           TubeGeometry 发光管道 + 沿线流动数据包 + 拖拽旋转
 * three.js r149 UMD，与 hero.js 各自独立渲染器。
 * ============================================================ */
(function () {
  'use strict';

  function fail() {
    document.documentElement.className += ' no-webgl';
  }
  if (typeof THREE === 'undefined') { fail(); return; }
  var canvas = document.getElementById('topoScene');
  if (!canvas) { fail(); return; }

  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
  } catch (e) { fail(); return; }

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var PR = Math.min(window.devicePixelRatio || 1, 1.75);
  renderer.setPixelRatio(PR);
  renderer.setClearColor(0x000000, 0);

  var PLATFORMS = [
    { name: 'Claude Code', color: 0xFF6B35 },
    { name: 'Codex CLI', color: 0x10A37F },
    { name: 'OpenCode', color: 0x35D9FF },
    { name: 'Pi', color: 0xA78BFF },
    { name: 'Trae IDE', color: 0x4E8FFF },
    { name: 'WorkBuddy', color: 0xC6FF4A }
  ];

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(45, 1, 1, 3000);
  camera.position.set(0, 40, 440);
  camera.lookAt(0, 0, 0);
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

  function labelSprite(main, sub, colorHex, w) {
    var c = document.createElement('canvas');
    c.width = 512; c.height = 128;
    var g = c.getContext('2d');
    var col = '#' + ('000000' + colorHex.toString(16)).slice(-6);
    g.fillStyle = col;
    g.beginPath();
    g.arc(42, 46, 9, 0, 6.2832);
    g.fill();
    g.textBaseline = 'alphabetic';
    g.font = '700 40px Sora, "PingFang SC", "Microsoft YaHei", sans-serif';
    g.fillStyle = '#E9ECF2';
    g.fillText(main, 68, 58);
    if (sub) {
      g.font = '500 24px PlexMono, Consolas, monospace';
      g.fillStyle = 'rgba(154,164,180,0.9)';
      g.fillText(sub, 68, 98);
    }
    var sp = new THREE.Sprite(new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false
    }));
    sp.scale.set(w || 150, (w || 150) / 4, 1);
    return sp;
  }

  function glowSprite(color, size, opacity) {
    var sp = new THREE.Sprite(new THREE.SpriteMaterial({
      map: glowTex, color: color, transparent: true, opacity: opacity,
      blending: THREE.AdditiveBlending, depthWrite: false
    }));
    sp.scale.set(size, size, 1);
    return sp;
  }

  /* ── 中心：共享黑板 ──────────────────────────────────────── */
  var center = new THREE.Group();
  center.add(new THREE.Mesh(
    new THREE.SphereGeometry(26, 48, 48),
    new THREE.MeshBasicMaterial({ color: 0x0d1620 })
  ));
  center.add(new THREE.LineSegments(
    new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(36, 1)),
    new THREE.LineBasicMaterial({ color: 0x35D9FF, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false })
  ));
  center.add(glowSprite(0x9be8ff, 150, 0.5));
  var bbLabel = labelSprite('共享黑板 .agents/', '谁在干嘛 · 心跳 · 留言 · 交接', 0xFFFFFF, 220);
  bbLabel.position.set(0, -64, 0);
  center.add(bbLabel);
  var bbRing = new THREE.Mesh(
    new THREE.RingGeometry(0.975, 1.0, 128),
    new THREE.MeshBasicMaterial({ color: 0x35D9FF, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide })
  );
  bbRing.scale.set(52, 52, 1);
  center.add(bbRing);
  root.add(center);
  /* ── 平台节点 + 人类节点 + 管道 + 数据包 ─────────────────── */
  var packets = [];
  function addTube(from, mid, to, color, opacity) {
    var curve = new THREE.QuadraticBezierCurve3(from, mid, to);
    var tube = new THREE.Mesh(
      new THREE.TubeGeometry(curve, 48, 1.3, 8, false),
      new THREE.MeshBasicMaterial({ color: color, transparent: true, opacity: opacity, blending: THREE.AdditiveBlending, depthWrite: false })
    );
    root.add(tube);
    var ghost = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(curve.getPoints(64)),
      new THREE.LineBasicMaterial({ color: color, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending, depthWrite: false })
    );
    root.add(ghost);
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
    var ang = i * Math.PI / 3 - Math.PI / 2;
    var px = Math.cos(ang) * 150;
    var pz = Math.sin(ang) * 150;
    var py = Math.sin(i * 1.7) * 44;

    var node = new THREE.Group();
    node.position.set(px, py, pz);
    node.add(new THREE.Mesh(
      new THREE.SphereGeometry(9, 32, 32),
      new THREE.MeshBasicMaterial({ color: pf.color })
    ));
    node.add(glowSprite(pf.color, 54, 0.65));
    var label = labelSprite(pf.name, 'AGENTS.md', pf.color, 150);
    label.position.set(0, 34, 0);
    node.add(label);
    root.add(node);

    var from = new THREE.Vector3(px, py, pz);
    var to = new THREE.Vector3(0, 0, 0);
    var mid = from.clone().lerp(to, 0.5);
    mid.y += 74;
    addTube(from, mid, to, pf.color, 0.22);
  });

  /* 人类节点（琥珀色，从上方连入黑板） */
  var humanPos = new THREE.Vector3(0, 196, -30);
  var human = new THREE.Group();
  human.position.copy(humanPos);
  human.add(new THREE.Mesh(
    new THREE.SphereGeometry(11, 32, 32),
    new THREE.MeshBasicMaterial({ color: 0xFFB020 })
  ));
  human.add(glowSprite(0xFFB020, 64, 0.7));
  var hLabel = labelSprite('你 · 人类指令', '优先级最高', 0xFFB020, 170);
  hLabel.position.set(0, 36, 0);
  human.add(hLabel);
  root.add(human);
  addTube(humanPos, new THREE.Vector3(0, 96, -18), new THREE.Vector3(0, 0, 0), 0xFFB020, 0.22);

  /* ── 交互：拖拽旋转 + 视差 ───────────────────────────────── */
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
    targetRX = Math.max(-0.5, Math.min(0.5, targetRX + (e.clientY - lastY) * 0.003));
    lastX = e.clientX; lastY = e.clientY;
  });
  function endDrag() { dragging = false; }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('pointerleave', endDrag);

  /* ── 主循环 ──────────────────────────────────────────────── */
  var running = true, visible = false;
  document.addEventListener('visibilitychange', function () {
    running = !document.hidden;
  });
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (es) {
      visible = es[0].isIntersecting;
      if (visible && !running) running = true;
    }, { threshold: 0.05 }).observe(canvas);
  } else {
    visible = true;
  }
  window.addEventListener('resize', function () {
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  });
  window.dispatchEvent(new Event('resize'));

  function frame(dt, t) {
    curRY += (targetRY - curRY) * 0.08;
    curRX += (targetRX - curRX) * 0.08;
    if (!dragging && !reduceMotion) targetRY += dt * 0.12;
    root.rotation.y = curRY;
    root.rotation.x = curRX;
    bbRing.scale.set(52, 52, 1);
    bbRing.quaternion.copy(camera.quaternion);
    for (var i = 0; i < packets.length; i++) {
      var p = packets[i];
      p.t = (p.t + dt * p.speed) % 1;
      var pos = p.curve.getPointAt(p.t);
      var f = Math.sin(p.t * Math.PI);
      p.sprite.position.copy(pos);
      p.sprite.material.opacity = 0.25 + 0.7 * f;
      var s = 7 + 7 * f;
      p.sprite.scale.set(s, s, 1);
    }
    renderer.render(scene, camera);
  }

  if (reduceMotion) {
    frame(0.016, 0.016);
  } else {
    var last = performance.now();
    (function loop() {
      requestAnimationFrame(loop);
      if (!running || !visible) { last = performance.now(); return; }
      var now = performance.now();
      var dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      frame(dt, now / 1000);
    })();
  }
})();
