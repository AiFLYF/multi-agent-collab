(function () {
  'use strict';
  var initialSessions = [
    {id: 'codex-demo', platform: 'Codex', color: '#62dfda', task: '完善按平台安装流程', branch: 'feature/platform-install', status: 'active', note: '正在验证最小安装与平台选择的迁移。', age: '2 分钟前更新'},
    {id: 'claude-demo', platform: 'Claude Code', color: '#eaa282', task: '补充项目使用文档', branch: 'feature/onboarding-docs', status: 'active', note: '与安装任务协调，暂不修改安装器源码。', age: '1 分钟前更新'},
    {id: 'cursor-demo', platform: 'Cursor', color: '#b8a8fa', task: '优化移动端展示', branch: 'feature/mobile-demo', status: 'active', note: '仅改演示页面，已登记需要修改的文件。', age: '3 分钟前更新'},
    {id: 'trae-demo', platform: 'Trae', color: '#f0bb71', task: '等待接口约定确认', branch: 'feature/status-api', status: 'blocked', note: 'inbox 已留问题，确认后再调整接口字段。', age: '4 分钟前更新'}
  ];
  var stateLabels = {active: '活跃', blocked: '等待确认', done: '已完成'};
  var sessions, messages, timeline;
  var cards = document.getElementById('demo-sessions');
  var target = document.getElementById('directive-target');
  var body = document.getElementById('directive-body');
  var filter = document.getElementById('session-filter');
  var status = document.getElementById('demo-status');

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function reset() {
    sessions = initialSessions.map(function (s) { return Object.assign({}, s); });
    ['install.py', 'README.md', 'site/assets/css/style.css', 'api/status.py'].forEach(function (file, i) { sessions[i].file = file; });
    status.dataset.kind = '';
    messages = [{target: '所有会话', text: '今天优先验证安装流程，完成后请补充测试结果。', reply: '已收到。各会话会在自己的工作范围内处理，完成后留下记录。'}];
    timeline = [{time: stamp(-4), text: 'Cursor 登记了移动端展示任务与涉及文件。'}, {time: stamp(-6), text: 'Claude Code 读取共享总章，确认文档改动范围。'}, {time: stamp(-8), text: 'Codex 完成开工检查，建立安装流程任务。'}];
    filter.value = 'all';
    body.value = '';
    target.replaceChildren(node('option', '', '所有会话'));
    target.firstChild.value = 'any';
    sessions.forEach(function (s) {
      var option = node('option', '', s.platform + ' · ' + s.task);
      option.value = s.id;
      target.appendChild(option);
    });
    render();
  }
  function stamp(minutes) {
    return new Date(Date.now() + (minutes || 0) * 60000).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', hour12: false});
  }
  function renderCards() {
    var shown = sessions.filter(function (s) { return filter.value === 'all' || s.status === filter.value; });
    var focused = document.activeElement;
    var focusId = focused && focused.dataset ? focused.dataset.session : null;
    var fragment = document.createDocumentFragment();
    shown.forEach(function (s) {
      var card = node('article', 'session-card' + (s.status === 'done' ? ' is-done' : ''));
      card.style.setProperty('--agent-color', s.color);
      var top = node('div', 'session-top');
      top.appendChild(node('span', 'platform-name', s.platform));
      var label = node('span', 'state-label');
      var dot = node('i', 'dot ' + s.status);
      dot.setAttribute('aria-hidden', 'true');
      label.appendChild(dot);
      label.appendChild(document.createTextNode(stateLabels[s.status]));
      top.appendChild(label);
      card.appendChild(top);
      card.appendChild(node('h3', '', s.task));
      card.appendChild(node('p', 'branch mono', s.branch));
      card.appendChild(node('p', 'claimed-files mono', (s.status === 'done' ? '已释放：' : '文件占用：') + s.file));
      card.appendChild(node('p', 'session-note', s.note));
      var bottom = node('div', 'session-bottom');
      bottom.appendChild(node('span', '', s.age));
      var button = node('button', 'text-button', s.status === 'done' ? '已记录完成 ✓' : '标记完成 ↗');
      button.type = 'button';
      button.dataset.session = s.id;
      button.setAttribute('aria-label', (s.status === 'done' ? '已完成：' : '标记完成：') + s.task);
      button.disabled = s.status === 'done';
      button.addEventListener('click', function () {
        s.status = 'done';
        s.age = '刚刚更新';
        s.note = '演示任务已收尾，完成记录已追加到共享时间线。';
        timeline.unshift({time: stamp(), text: s.platform + ' 完成了「' + s.task + '」，已记录收尾。'});
        render();
        status.dataset.kind = '';
        status.textContent = s.platform + ' 的任务已在演示中标记完成。';
        // 完成按钮会禁用或被筛选掉，把焦点移到稳定的状态筛选器。
        filter.focus();
      });
      bottom.appendChild(button);
      card.appendChild(bottom);
      fragment.appendChild(card);
    });
    cards.replaceChildren(fragment);
    document.getElementById('session-empty').hidden = shown.length > 0;
    if (focusId) {
      var replacement = cards.querySelector('[data-session="' + focusId + '"]');
      if (replacement && !replacement.disabled) replacement.focus();
    }
  }
  function render() {
    document.getElementById('metric-total').textContent = sessions.length;
    ['active', 'blocked', 'done'].forEach(function (key) {
      document.getElementById('metric-' + key).textContent = sessions.filter(function (s) { return s.status === key; }).length;
    });
    renderCards();
    var items = document.createDocumentFragment();
    messages.slice(0, 12).forEach(function (message) {
      var item = node('article', 'message');
      var meta = node('div', 'message-meta');
      meta.appendChild(node('span', '', '你 → ' + message.target));
      meta.appendChild(node('span', '', '演示 · 已回复'));
      item.appendChild(meta);
      item.appendChild(node('p', '', message.text));
      var reply = node('div', 'message-reply');
      reply.appendChild(node('span', '', '模拟回复'));
      reply.appendChild(node('p', '', message.reply));
      item.appendChild(reply);
      items.appendChild(item);
    });
    document.getElementById('demo-messages').replaceChildren(items);
    document.getElementById('reply-count').textContent = messages.length + ' 条';
    var events = document.createDocumentFragment();
    timeline.slice(0, 12).forEach(function (entry) {
      var item = node('li');
      item.appendChild(node('time', '', entry.time));
      item.appendChild(node('span', '', entry.text));
      events.appendChild(item);
    });
    document.getElementById('demo-timeline').replaceChildren(events);
  }
  filter.addEventListener('change', renderCards);
  document.getElementById('demo-reset').addEventListener('click', function () {
    reset();
    status.textContent = '演示已恢复初始状态，可以再次体验。';
  });
  document.getElementById('demo-conflict').addEventListener('click', function () {
    var owner = sessions.find(function (s) { return s.status !== 'done'; });
    status.dataset.kind = 'warning';
    if (!owner) { status.textContent = '所有演示会话均已收尾，当前没有文件占用。'; return; }
    var text = '登记被阻止：' + owner.file + ' 已由 ' + owner.platform + ' 占用，新会话没有取得该文件。';
    timeline.unshift({time: stamp(), text: text});
    render();
    status.textContent = text + ' 请先协调或等原会话收尾。';
  });
  document.getElementById('demo-directive').addEventListener('submit', function (event) {
    event.preventDefault();
    var text = body.value.trim();
    if (!text) { body.setCustomValidity('请输入一条演示指令。'); body.reportValidity(); return; }
    var session = sessions.find(function (s) { return s.id === target.value; });
    var recipient = session ? session.platform : '所有会话';
    messages.unshift({target: recipient, text: text, reply: '已收到这条演示指令。真实使用时，agent 在下一次读取 inbox 后处理并回复。'});
    timeline.unshift({time: stamp(), text: '你向 ' + recipient + ' 发送了一条演示指令，模拟回复已生成。'});
    body.value = '';
    render();
    status.dataset.kind = '';
    status.textContent = '已模拟发送给 ' + recipient + '，请查看「指令与回复」。';
  });
  body.addEventListener('input', function () { body.setCustomValidity(''); });
  reset();
})();
