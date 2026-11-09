(function () {
  'use strict';
  var initialSessions = [
    {id: 'codex-demo', platform: 'Codex', color: '#62dfda', task: '完善按平台安装流程', branch: 'feature/platform-install', workspace: 'demo-shop', file: 'install.py', status: 'active', note: '已回复验证计划，等待补充原消息的实际结果。', age: '2 分钟前更新'},
    {id: 'claude-demo', platform: 'Claude Code', color: '#eaa282', task: '补充项目使用文档', branch: 'feature/onboarding-docs', workspace: 'demo-shop-docs', file: 'README.md', status: 'active', note: '已确认文档范围，与其它工作区协调。', age: '1 分钟前更新'},
    {id: 'cursor-demo', platform: 'Cursor', color: '#b8a8fa', task: '优化移动端展示', branch: 'feature/mobile-demo', workspace: 'demo-shop-mobile', file: 'site/assets/css/style.css', status: 'active', note: '正在自己的已登记 worktree 中处理展示。', age: '3 分钟前更新'},
    {id: 'trae-demo', platform: 'Trae', color: '#f0bb71', task: '等待接口约定确认', branch: 'feature/status-api', workspace: 'demo-shop-api', file: 'api/status.py', status: 'blocked', note: '接口约定尚待确认，blocked 保留文件占用。', age: '4 分钟前更新'},
    {id: 'pi-demo', platform: 'Pi', color: '#a8c5ef', task: '上一任务：完成安装体检', lastTask: '完成安装体检', branch: 'feature/install-check', workspace: 'demo-shop-checks', file: '', lastFiles: ['tests/install_check.py'], status: 'standby', note: '上一任务已记录结果，无文件占用，等待新任务。', age: '5 分钟前活动'}
  ];
  var stateLabels = {active: '执行中', blocked: '受阻 / 暂停', standby: '待机', done: '已结束'};
  var actionLabels = {message: '普通留言', pause: '暂停', cancel: '取消', avoid: '避让'};
  var stageLabels = {sent: '已发送 · 待读', notified: '已提示 · 待读', read: '已读 · 待回复', planned: '已回复计划 · open', completed: '普通事项已完成 · answered', controlled: '控制已执行 · 仍生效', released: '人类已解除 · closed'};
  var sessions, messages, timeline, nextMessage, activationId;
  var cards = document.getElementById('demo-sessions');
  var target = document.getElementById('directive-target');
  var body = document.getElementById('directive-body');
  var action = document.getElementById('directive-action');
  var filter = document.getElementById('session-filter');
  var search = document.getElementById('session-search');
  var messageFilter = document.getElementById('message-filter');
  var status = document.getElementById('demo-status');
  var send = document.getElementById('directive-send');
  var toolSession = document.getElementById('demo-tool-session');
  var activationPanel = document.getElementById('demo-activation-panel');
  var activationForm = document.getElementById('demo-activation');
  var activationTask = document.getElementById('activation-task');
  var activationBranch = document.getElementById('activation-branch');
  var activationFiles = document.getElementById('activation-files');

  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }
  function button(text, callback, focusKey) {
    var el = node('button', 'text-button', text);
    el.type = 'button';
    if (focusKey) el.dataset.focusKey = focusKey;
    el.addEventListener('click', callback);
    return el;
  }
  function inform(text, kind) {
    status.dataset.kind = kind || '';
    status.textContent = text;
  }
  function stamp(minutes) {
    return new Date(Date.now() + (minutes || 0) * 60000).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', hour12: false});
  }
  function log(text) {
    timeline.unshift({time: stamp(), text: text});
    if (timeline.length > 40) timeline.pop();
  }
  function applies(message, session) {
    return message.targets.indexOf(session.id) !== -1;
  }
  function activeControls(session) {
    return messages.filter(function (m) { return m.action !== 'message' && !m.controlReleased && applies(m, session); });
  }
  function pending(session) {
    return messages.filter(function (m) { return m.inboxStatus === 'open' && applies(m, session); });
  }
  function scope(session) {
    return session.files ? session.files.slice() : (session.file ? [session.file] : []);
  }
  function claimsFiles(session) {
    return session.status === 'active' || session.status === 'blocked';
  }
  function recordActivity(session, command) {
    if (!session || session.status === 'done') return;
    session.activityRevision = (session.activityRevision || 0) + 1;
    session.lastCommand = command;
    session.age = '刚刚活动 · ' + new Date().toLocaleTimeString('zh-CN', {hour12: false});
  }
  function messageActor(message) {
    return sessions.find(function (s) { return s.id === toolSession.value && s.status !== 'done' && applies(message, s); }) ||
      sessions.find(function (s) { return s.status !== 'done' && applies(message, s); });
  }
  function messageStage(message, actor) {
    if (message.controlReleased) return 'released';
    if (message.action === 'message' && message.inboxStatus === 'answered') return 'completed';
    if (actor && message.stagesBy && message.stagesBy[actor.id]) return message.stagesBy[actor.id];
    return message.targets.length === 1 ? message.stage : 'sent';
  }
  function setMessageStage(message, actor, stage) {
    message.stage = stage;
    if (!message.stagesBy) message.stagesBy = {};
    if (actor) message.stagesBy[actor.id] = stage;
  }
  function pathOverlap(a, b) {
    a = a.toLowerCase(); b = b.toLowerCase();
    return a === b || (a.slice(-1) === '/' && b.indexOf(a) === 0) || (b.slice(-1) === '/' && a.indexOf(b) === 0);
  }
  function taskConflict(session, branch, files) {
    return sessions.find(function (other) {
      return other.id !== session.id && claimsFiles(other) && (other.workspace === session.workspace || other.branch === branch ||
        scope(other).some(function (owned) { return files.some(function (file) { return pathOverlap(owned, file); }); }));
    });
  }
  function closeActivation() {
    activationId = null;
    activationPanel.hidden = true;
  }
  function openActivation(session) {
    if (session.status !== 'standby') return;
    activationId = session.id;
    activationPanel.hidden = false;
    document.getElementById('activation-owner').textContent = session.platform + ' · ' + session.id + '；当前无文件占用。';
    activationTask.value = '补充 ' + session.platform + ' 的新任务验证';
    activationBranch.value = 'feature/' + session.id + '-task-' + ((session.taskNumber || 1) + 1);
    activationFiles.value = (session.lastFiles || ['tests/new_task.py']).join('\n');
    toolSession.value = session.id;
    renderMessages();
    document.getElementById('activation-status').textContent = '核对新任务和完整范围；相关消息先读取并回复计划，任务完成后再回复结果。';
    activationPanel.scrollIntoView({block: 'start'});
    activationTask.focus({preventScroll: true});
  }
  function reset() {
    sessions = initialSessions.map(function (s) { return Object.assign({}, s, {activityRevision: 0, taskNumber: 1}); });
    messages = [{id: 'DEMO-01', targets: ['codex-demo'], target: 'Codex', action: 'message', text: '安装流程验证完成后，请在原消息补充实际结果。', stage: 'planned', inboxStatus: 'open', plan: '先验证最小安装与平台选择，再回报结果（模拟计划）。'}];
    timeline = [{time: stamp(-2), text: 'Codex 回复验证计划，原人类事项仍为 open。'}, {time: stamp(-4), text: 'Cursor 在独立且已登记的 worktree 中确认了文件范围。'}, {time: stamp(-5), text: 'Pi 完成安装体检并 finish --standby（模拟），释放范围等待新任务。'}, {time: stamp(-6), text: 'Claude Code 读取共享总章，登记文档任务。'}];
    nextMessage = 2;
    filter.value = 'all';
    search.value = '';
    messageFilter.value = 'all';
    action.value = 'message';
    target.value = 'any';
    toolSession.value = 'codex-demo';
    body.value = '';
    body.setCustomValidity('');
    closeActivation();
    document.getElementById('demo-notifications').textContent = '暂无新提示。选一个会话，模拟它正常调用工具。';
    updateKind();
    updateLength();
    render();
    inform('Codex 已回复计划，事项仍待完成；Pi 已待机。试着回复原消息结果，或给待机会话登记新任务。');
  }
  function showMessages() {
    messageFilter.value = 'all';
    renderMessages();
    document.getElementById('replies-title').scrollIntoView({block: 'start', behavior: 'auto'});
    messageFilter.focus({preventScroll: true});
  }
  function composeFor(session) {
    target.value = session.id;
    toolSession.value = session.id;
    renderMessages();
    document.getElementById('directive-title').scrollIntoView({block: 'start', behavior: 'auto'});
    body.focus({preventScroll: true});
  }
  function finishTask(session) {
    if (session.status !== 'active' || activeControls(session).length || pending(session).length) return;
    session.lastFiles = scope(session);
    session.lastTask = session.task;
    session.files = [];
    session.file = '';
    session.status = 'standby';
    recordActivity(session, 'finish --standby');
    session.note = '上一任务已模拟验证并收尾，文件占用已释放；等待新任务，登记成功后再开始。';
    log(session.platform + ' 完成 finish --standby（模拟），保存任务结果、释放完整范围，保留对话 ID。');
    render();
    inform(session.platform + ' 的演示任务已收尾并待机，已释放 ' + session.lastFiles.join('、') + '。新任务必须重新登记范围。');
    filter.focus();
  }
  function endStandby(session) {
    if (session.status !== 'standby' || activeControls(session).length || pending(session).length) return;
    recordActivity(session, 'finish');
    session.status = 'done';
    session.note = '已明确结束待机，保留原任务记录；done 不能复活，新对话需要新 ID。';
    if (activationId === session.id) closeActivation();
    log(session.platform + ' 明确结束待机（模拟），未重复宣称原任务重新验证。');
    render();
    inform(session.platform + ' 已结束对话身份；原任务与结果保留，不能用 activate 或普通调用复活。');
    filter.focus();
  }
  function resumeTask(session) {
    if (session.status !== 'blocked' || activeControls(session).length || pending(session).length) return;
    var owner = taskConflict(session, session.branch, scope(session));
    if (owner) { inform('恢复被阻止：任务分支或文件范围仍被 ' + owner.platform + ' 占用，请先协调。', 'warning'); return; }
    session.status = 'active';
    recordActivity(session, 'resume');
    session.note = '你已在演示中明确确认恢复，模拟按正常 resume 流程继续。';
    log('你明确确认 ' + session.platform + ' 恢复；会话通过检查后继续推进（模拟）。');
    render();
    inform(session.platform + ' 已在演示中恢复。真实恢复需核对原控制、分支、占用与授权。');
    filter.focus();
  }
  function renderCards() {
    var query = search.value.trim().toLowerCase();
    var shown = sessions.filter(function (s) {
      return (filter.value === 'all' || s.status === filter.value) &&
        (s.platform + ' ' + s.task + ' ' + s.branch + ' ' + s.file).toLowerCase().indexOf(query) !== -1;
    });
    var fragment = document.createDocumentFragment();
    shown.forEach(function (s) {
      var controls = activeControls(s);
      var outstanding = pending(s);
      var card = node('article', 'session-card' + (s.status === 'done' ? ' is-done' : s.status === 'standby' ? ' is-standby' : ''));
      card.dataset.session = s.id;
      card.dataset.status = s.status;
      card.dataset.activity = s.activityRevision || 0;
      card.dataset.files = JSON.stringify(scope(s));
      card.tabIndex = -1;
      card.style.setProperty('--agent-color', s.color);
      var top = node('div', 'session-top');
      top.appendChild(node('span', 'platform-name', s.platform));
      var state = node('span', 'state-label');
      var dot = node('i', 'dot ' + s.status);
      dot.setAttribute('aria-hidden', 'true');
      state.appendChild(dot);
      state.appendChild(document.createTextNode(stateLabels[s.status]));
      top.appendChild(state);
      card.appendChild(top);
      card.appendChild(node('h3', '', s.task));
      card.appendChild(node('p', 'branch mono', s.branch));
      card.appendChild(node('p', 'workspace mono', 'worktree: ' + s.workspace));
      card.appendChild(node('p', 'claimed-files mono', claimsFiles(s) ? '保留占用：' + scope(s).join('、') : '无文件占用' + (s.lastFiles ? ' · 上次释放：' + s.lastFiles.join('、') : '')));
      if (controls.length) card.appendChild(node('p', 'control-badge', '控制生效：' + controls.map(function (m) { return actionLabels[m.action]; }).join('、')));
      if (outstanding.length) card.appendChild(button(outstanding.length + ' 条人类事项待处理 →', showMessages, s.id + ':messages'));
      card.appendChild(node('p', 'session-note', s.note));
      if (s.taskNumber > 1 && s.lastTask && s.status === 'active') card.appendChild(node('p', 'activity-kind', '上一已收尾任务：' + s.lastTask));
      if (s.lastCommand) card.appendChild(node('p', 'activity-kind mono', '最近调用：' + s.lastCommand));
      var bottom = node('div', 'session-bottom');
      bottom.appendChild(node('span', '', s.age));
      var actions = node('div', 'session-actions');
      if (s.status !== 'done') actions.appendChild(button('发消息', function () { composeFor(s); }, s.id + ':compose'));
      var complete = button(s.status === 'done' ? '已结束 ✓' : s.status === 'standby' ? '接新任务，登记范围' : (s.status === 'blocked' ? '模拟确认并恢复' : '完成任务并待机'), function () {
        if (s.status === 'blocked') resumeTask(s); else if (s.status === 'standby') openActivation(s); else finishTask(s);
      }, s.id + ':task');
      complete.setAttribute('aria-label', (s.status === 'blocked' ? '模拟确认并恢复：' : s.status === 'standby' ? '给待机会话登记新任务：' : '完成演示任务并待机：') + s.task);
      complete.disabled = s.status === 'done' || controls.length > 0 || (s.status !== 'standby' && outstanding.length > 0);
      actions.appendChild(complete);
      if (s.status === 'standby') {
        var close = button('结束对话', function () { endStandby(s); }, s.id + ':close');
        close.disabled = controls.length > 0 || outstanding.length > 0;
        actions.appendChild(close);
      }
      bottom.appendChild(actions);
      card.appendChild(bottom);
      if (s.status !== 'done' && complete.disabled) card.appendChild(node('p', 'completion-note', controls.length ? '先处理控制；回复不会解除控制，也不会释放文件。' : '先在原消息回复结果并结束事项，再完成任务。'));
      fragment.appendChild(card);
    });
    cards.replaceChildren(fragment);
    document.getElementById('session-empty').hidden = shown.length > 0;
    document.getElementById('session-search-status').textContent = '显示 ' + shown.length + ' / ' + sessions.length + ' 个会话';
  }
  function renderTargets() {
    var selected = target.value;
    var options = document.createDocumentFragment();
    var any = node('option', '', '所有未结束会话（含待机）');
    any.value = 'any';
    options.appendChild(any);
    sessions.forEach(function (s) {
      var option = node('option', '', s.platform + ' · ' + (s.status === 'done' ? '已结束' : s.status === 'standby' ? '待机 · 可接新任务' : s.task));
      option.value = s.id;
      option.disabled = s.status === 'done';
      options.appendChild(option);
    });
    target.replaceChildren(options);
    var old = sessions.find(function (s) { return s.id === selected && s.status !== 'done'; });
    target.value = old ? selected : 'any';
    target.disabled = send.disabled = sessions.every(function (s) { return s.status === 'done'; });
  }
  function renderToolSessions() {
    var selected = toolSession.value;
    var options = document.createDocumentFragment();
    sessions.forEach(function (s) {
      var option = node('option', '', s.platform + ' · ' + stateLabels[s.status]);
      option.value = s.id; option.disabled = s.status === 'done'; options.appendChild(option);
    });
    toolSession.replaceChildren(options);
    var chosen = sessions.find(function (s) { return s.id === selected && s.status !== 'done'; }) || sessions.find(function (s) { return s.status !== 'done'; });
    if (chosen) toolSession.value = chosen.id;
    toolSession.disabled = document.getElementById('demo-tool-check').disabled = !chosen;
  }
  function actOnMessage(message, step) {
    var actor = messageActor(message);
    var recipients = actor ? [actor] : [];
    if (step !== 'release' && !actor) return;
    if (step !== 'release') recordActivity(actor, step === 'read' ? 'agents_messages read' : '原 inbox 回复');
    if (step === 'read') {
      setMessageStage(message, actor, 'read');
      log(actor.platform + ' 完整读取 ' + message.id + '；仅记录本人活动，标读与事项完成分开（模拟）。');
      if (message.action !== 'message') {
        recipients.forEach(function (s) {
          if (s.status === 'active') s.status = 'blocked';
          s.note = s.status === 'standby' ? '待机仍无文件占用；控制解除前不能登记新任务。' : '已按' + actionLabels[message.action] + '要求停止相应推进；原文件与占用保留，等待后续明确指令。';
        });
        inform('已模拟读取并执行' + actionLabels[message.action] + '。' + (actor.status === 'standby' ? '待机仍无占用，不能接新任务；' : '会话保留原占用；') + '接下来回复控制处理结果。', 'warning');
      } else inform('已模拟标读。事项仍为 open，请先回复计划，处理后再回复结果。');
    } else if (step === 'plan') {
      setMessageStage(message, actor, 'planned');
      message.plan = '已收到，先按已登记范围处理，再在原消息回报具体结果（模拟计划）。';
      message.planBy = actor.platform;
      log(message.target + ' 回复 ' + message.id + ' 的计划，事项保持 open。');
      inform('计划已回复。收到与回复计划都不表示事项完成。');
    } else if (step === 'result') {
      setMessageStage(message, actor, 'completed');
      message.inboxStatus = 'answered';
      message.result = '已按要求处理并记录结果（模拟）。真实使用需写明具体结果与实际测试证据。';
      log(message.target + ' 回复 ' + message.id + ' 的结果并将普通事项置 answered；任务状态未改变。');
      inform('普通事项已在演示中完成，任务仍保持原状态；可以继续处理或完成任务。');
    } else if (step === 'control-reply') {
      setMessageStage(message, actor, 'controlled');
      message.inboxStatus = 'answered';
      message.result = '已执行' + actionLabels[message.action] + '，' + (actor.status === 'standby' ? '待机仍无文件占用' : '原提交、未提交文件与占用均保留') + '（模拟）。控制仍生效，等待人类明确解除或替代。';
      log(message.target + ' 回复控制处理结果；' + message.id + ' 即使 answered 仍生效。');
      inform('控制结果已回复，控制仍生效。只有你明确解除后，才能按正常流程恢复。', 'warning');
    } else if (step === 'release') {
      message.stage = 'released';
      message.inboxStatus = 'closed';
      message.controlReleased = true;
      log('你明确解除 ' + message.id + '，旧控制置 closed；会话状态不自动改变。');
      inform('你已明确解除控制。受阻任务仍需确认并恢复；待机会话仍无占用，新任务需重新登记。');
    }
    render();
    var article = document.querySelector('[data-message="' + message.id + '"]');
    if (article) article.focus({preventScroll: true}); else messageFilter.focus();
  }
  function renderMessages() {
    var shown = messages.filter(function (m) {
      if (messageFilter.value === 'open') return m.inboxStatus === 'open';
      if (messageFilter.value === 'complete') return m.action === 'message' && m.inboxStatus === 'answered';
      if (messageFilter.value === 'control') return m.action !== 'message' && !m.controlReleased;
      return true;
    });
    var fragment = document.createDocumentFragment();
    shown.forEach(function (m) {
      var actor = messageActor(m);
      var stage = messageStage(m, actor);
      var item = node('article', 'message' + (m.action !== 'message' && !m.controlReleased ? ' is-control' : ''));
      item.dataset.message = m.id;
      item.dataset.stage = stage;
      item.tabIndex = -1;
      var meta = node('div', 'message-meta');
      meta.appendChild(node('span', '', '你 → ' + m.target + ' · ' + actionLabels[m.action]));
      meta.appendChild(node('span', 'message-state', stageLabels[stage]));
      item.appendChild(meta);
      item.appendChild(node('p', 'message-id mono', m.id + ' · inbox ' + m.inboxStatus + ' · 模拟'));
      item.appendChild(node('p', 'message-body', m.text));
      if (actor) item.appendChild(node('p', 'message-actor', '此条模拟执行者：' + actor.platform + ' · ' + actor.id));
      if (stage === 'sent' || stage === 'notified') item.appendChild(node('p', 'message-hint', '已写入模拟 inbox，此执行者尚未读取。可先模拟业务工具调用，查看短提示。'));
      if (stage === 'read') item.appendChild(node('p', 'message-hint', m.action === 'message' ? '完整读取只保存阅读回执，原事项仍为 open。' : '控制要求已立即处理，任务停止相应推进；仍需回原消息回复结果。'));
      if (m.plan) {
        var plan = node('div', 'message-reply');
        plan.appendChild(node('span', '', (m.planBy || m.target) + ' · 模拟计划回复'));
        plan.appendChild(node('p', '', m.plan));
        item.appendChild(plan);
      }
      if (m.result) {
        var result = node('div', 'message-reply');
        result.appendChild(node('span', '', '模拟结果回复'));
        result.appendChild(node('p', '', m.result));
        item.appendChild(result);
      }
      if (m.action !== 'message') item.appendChild(node('p', 'control-explanation', m.controlReleased ? '你已明确解除旧控制。会话状态不自动改变；恢复原任务或登记新任务前仍需核对。' : '回复、标读或任务确认均不能解除此控制；原有占用保留，待机也不能越过控制接新任务。'));
      var actions = node('div', 'message-actions');
      function add(text, step) {
        actions.appendChild(button(text, function () { actOnMessage(m, step); }, m.id + ':' + step));
      }
      if (actor && (stage === 'sent' || stage === 'notified')) add(m.action === 'message' ? '模拟完整读取' : '模拟读取并执行' + actionLabels[m.action], 'read');
      else if (actor && stage === 'read') add(m.action === 'message' ? '模拟回复计划' : '模拟回复控制结果', m.action === 'message' ? 'plan' : 'control-reply');
      else if (actor && stage === 'planned') add('模拟回复结果，完成事项', 'result');
      else if (stage === 'controlled') add('我明确解除此控制', 'release');
      item.appendChild(actions);
      fragment.appendChild(item);
    });
    document.getElementById('demo-messages').replaceChildren(fragment);
    document.getElementById('messages-empty').hidden = shown.length > 0;
    document.getElementById('reply-count').textContent = messages.length + ' 条 · ' + messages.filter(function (m) { return m.inboxStatus === 'open'; }).length + ' 条待处理';
  }
  function render() {
    document.getElementById('metric-total').textContent = sessions.length;
    ['active', 'blocked', 'standby', 'done'].forEach(function (key) {
      document.getElementById('metric-' + key).textContent = sessions.filter(function (s) { return s.status === key; }).length;
    });
    renderCards();
    renderTargets();
    renderToolSessions();
    renderMessages();
    var events = document.createDocumentFragment();
    timeline.forEach(function (entry) {
      var item = node('li');
      item.appendChild(node('time', '', entry.time));
      item.appendChild(node('span', '', entry.text));
      events.appendChild(item);
    });
    document.getElementById('demo-timeline').replaceChildren(events);
  }
  function updateLength() {
    document.getElementById('directive-length').textContent = body.value.length + ' / 400 字';
  }
  function updateKind() {
    document.getElementById('directive-kind-note').textContent = action.value === 'message'
      ? '普通留言先回复计划，处理完再回复结果。'
      : action.value === 'avoid'
        ? '请写明需要避让的文件。演示会让目标停止相应推进，并保留文件占用。'
        : '控制要求立即处理；即便回复结果，也要由你明确解除后才能恢复。';
  }
  filter.addEventListener('change', renderCards);
  search.addEventListener('input', renderCards);
  messageFilter.addEventListener('change', renderMessages);
  toolSession.addEventListener('change', renderMessages);
  document.getElementById('session-filter-clear').addEventListener('click', function () {
    filter.value = 'all'; search.value = ''; renderCards(); search.focus();
  });
  document.getElementById('demo-reset').addEventListener('click', function () {
    reset(); inform('演示已恢复初始状态，可以再次体验。');
  });
  document.getElementById('demo-conflict').addEventListener('click', function () {
    var owner = sessions.find(claimsFiles);
    if (!owner) { inform('所有演示会话均已收尾，当前没有文件占用。'); return; }
    var text = '登记被阻止：' + owner.file + ' 已由 ' + owner.platform + ' 占用，新会话未取得该文件。';
    log(text + '（模拟）'); render();
    inform(text + ' 先协调，blocked 与控制状态也不会释放占用。', 'warning');
  });
  document.getElementById('demo-tool-check').addEventListener('click', function () {
    var actor = sessions.find(function (s) { return s.id === toolSession.value && s.status !== 'done'; });
    if (!actor) return;
    var unread = messages.filter(function (m) { var stage = messageStage(m, actor); return applies(m, actor) && (stage === 'sent' || stage === 'notified'); });
    unread.forEach(function (m) { setMessageStage(m, actor, 'notified'); });
    var open = pending(actor).length;
    var controls = activeControls(actor).length;
    recordActivity(actor, 'agents_status --session');
    document.getElementById('demo-notifications').textContent = actor.platform + ' 本人调用已核验，只更新该会话活动。未读 ' + unread.length + ' · 人类未处理 ' + open + ' · 生效控制 ' + controls + '。' + (unread.length ? '请按需读取正文。' : open ? '已读的 open 事项请继续处理并回复结果。' : '暂无相关新消息。');
    log(actor.platform + ' 模拟本人业务调用：记录活动并返回相关短提示，未读 ' + unread.length + ' 条；其它会话不续活。');
    render();
    inform(actor.platform + ' 的最近活动已更新，任务状态仍为' + stateLabels[actor.status] + '。查看页面和筛选不会续活。');
  });
  document.getElementById('activation-cancel').addEventListener('click', function () {
    closeActivation(); filter.focus();
  });
  document.getElementById('activation-conflict').addEventListener('click', function () {
    var owner = sessions.find(claimsFiles);
    if (!owner) { document.getElementById('activation-status').textContent = '当前没有已占用文件，可直接核对并登记。'; return; }
    activationFiles.value = scope(owner)[0];
    document.getElementById('activation-status').textContent = '已填入 ' + owner.platform + ' 当前占用的文件；点击登记验证拦截，随后更换范围或先协调。';
    activationFiles.focus();
  });
  activationForm.addEventListener('submit', function (event) {
    event.preventDefault();
    var session = sessions.find(function (s) { return s.id === activationId; });
    var feedback = document.getElementById('activation-status');
    function reject(text, field) { feedback.textContent = text; if (field) field.focus(); }
    if (!session || session.status !== 'standby') { reject('目标不再待机，请重新选择；新任务未登记。'); return; }
    if (activeControls(session).length) { reject('控制仍生效；先读取并处理原控制，明确解除后再登记新任务。'); return; }
    if (pending(session).some(function (m) { return m.action !== 'message' || messageStage(m, session) !== 'planned'; })) {
      reject('相关原消息尚未完整读取并回复计划；先纳入新要求，再登记任务。'); return;
    }
    var task = activationTask.value.trim();
    var branch = activationBranch.value.trim();
    if (!task) { reject('请填写明确的新任务。', activationTask); return; }
    if (!/^(feature|release|hotfix)\/[A-Za-z0-9][A-Za-z0-9._\/-]*$/.test(branch) || branch.indexOf('//') !== -1 || branch.split('/').some(function (p) { return p === '.' || p === '..'; })) {
      reject('分支不符合演示策略；使用 feature/、release/ 或 hotfix/ 下的任务分支。', activationBranch); return;
    }
    var files = activationFiles.value.split(/\r?\n|,/).map(function (p) { return p.trim().replace(/\\/g, '/'); }).filter(Boolean);
    if (!files.length || files.some(function (p) { return p.charAt(0) === '/' || /[\u0000-\u001f:*?"<>|]/.test(p) || p.indexOf('//') !== -1 || p.split('/').some(function (part) { return part === '.' || part === '..'; }); })) {
      reject('填写完整的相对文件或目录范围，每行一个；不能使用绝对路径、通配符或越界路径。', activationFiles); return;
    }
    files = files.filter(function (p, index) { return files.indexOf(p) === index; });
    var owner = taskConflict(session, branch, files);
    if (owner) { reject('登记被阻止：分支或文件范围仍由 ' + owner.platform + ' 占用（' + scope(owner).join('、') + '）。该会话仍待机、范围为空。', activationFiles); return; }
    session.task = task; session.branch = branch; session.files = files; session.file = files.join('、');
    session.taskNumber = (session.taskNumber || 1) + 1;
    session.status = 'active';
    session.note = '新任务通过策略、原消息和范围核对后登记（模拟）；完成原事项与验证后再收尾待机。';
    recordActivity(session, 'activate');
    log(session.platform + ' activate 新任务（模拟）：' + task + '；重新登记完整范围 ' + files.join('、') + '。');
    closeActivation();
    filter.value = 'all'; search.value = '';
    render();
    inform(session.platform + ' 已重新登记新任务和 ' + files.length + ' 项范围，保留原对话 ID。普通调用不能替代这一步。');
    var card = document.querySelector('[data-session="' + session.id + '"]');
    if (card) { card.scrollIntoView({block: 'center'}); card.focus({preventScroll: true}); }
  });
  document.getElementById('demo-directive').addEventListener('submit', function (event) {
    event.preventDefault();
    var text = body.value.trim();
    if (!text || body.value.length > 400) {
      body.setCustomValidity(!text ? '请输入一条演示消息，不能只有空白。' : '演示消息最多 400 字。');
      body.reportValidity(); body.focus(); return;
    }
    var recipients = sessions.filter(function (s) { return s.status !== 'done' && (target.value === 'any' || target.value === s.id); });
    if (!recipients.length) { inform('没有可接收消息的未完成会话，请重置演示。', 'warning'); return; }
    var recipient = target.value === 'any' ? '所有未结束会话（含待机）' : recipients[0].platform;
    var id = 'DEMO-' + (nextMessage < 10 ? '0' : '') + nextMessage++;
    messages.unshift({id: id, targets: recipients.map(function (s) { return s.id; }), target: recipient, text: text, action: action.value, stage: 'sent', inboxStatus: 'open'});
    log('你向 ' + recipient + ' 发送 ' + id + '（模拟），目标尚未读取。');
    body.value = ''; updateLength();
    messageFilter.value = 'all';
    render();
    inform('已模拟写入 inbox，尚未读取。点「模拟工具检查消息」，再在下方读取与处理。');
    body.focus();
  });
  body.addEventListener('input', function () { body.setCustomValidity(''); updateLength(); });
  action.addEventListener('change', updateKind);
  Array.prototype.forEach.call(document.querySelectorAll('[data-demo-preset]'), function (preset) {
    preset.addEventListener('click', function () {
      var kind = preset.dataset.demoPreset;
      action.value = kind;
      var chosen = sessions.find(function (s) { return s.id === target.value; });
      var file = chosen && chosen.file ? chosen.file : 'README.md';
      body.value = kind === 'pause' ? '请暂停当前任务，保留原提交和未提交文件，等待我明确恢复。'
        : kind === 'avoid' ? '请避让 ' + file + '，停止相应修改，保留现场并回复结果。'
          : '请补充实际验证结果，完成后在原消息回复具体结果与测试证据。';
      body.setCustomValidity('');
      updateKind(); updateLength(); body.focus();
    });
  });
  var copyButton = document.getElementById('demo-copy-command');
  var copyTimer;
  copyButton.addEventListener('click', function () {
    var text = document.getElementById('demo-real-command').textContent;
    function feedback(ok) {
      clearTimeout(copyTimer);
      copyButton.textContent = ok ? '已复制 ✓' : '复制失败，请手动复制';
      inform(ok ? '真实命令已复制到剪贴板，请在已安装的目标项目中使用。' : '剪贴板不可用，请选择代码文字手动复制。', ok ? '' : 'warning');
      copyTimer = setTimeout(function () { copyButton.textContent = '复制命令'; }, 2800);
    }
    function fallback() {
      var previous = document.activeElement;
      var ta = node('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.left = '-10000px';
      document.body.appendChild(ta); ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { /* 保留手动复制入口 */ }
      ta.remove();
      if (previous) previous.focus();
      feedback(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { navigator.clipboard.writeText(text).then(function () { feedback(true); }, fallback); } catch (e) { fallback(); }
    } else fallback();
  });
  reset();
})();
