/* 按安装器提供的平台目录生成选择器；只在浏览器中预览，不执行安装。 */
(function () {
  'use strict';
  var data = window.MAC_INSTALL;
  var picker = document.getElementById('platform-picker');
  if (!data || !picker) return;
  var prompt = document.getElementById('prompt-agent');
  var command = document.getElementById('cmd-manual');
  var summary = document.getElementById('install-summary');
  var fileList = document.getElementById('install-file-list');
  var fileCount = document.getElementById('install-file-count');
  var clear = document.getElementById('platform-clear');
  var search = document.getElementById('platform-search');
  var empty = document.getElementById('platform-empty');
  var workflow = document.getElementById('install-workflow');
  var workflowNote = document.getElementById('install-workflow-note');
  var searchClear = document.getElementById('platform-search-clear');
  var searchStatus = document.getElementById('platform-search-status');
  var selectedList = document.getElementById('platform-selected');
  var presets = Array.prototype.slice.call(document.querySelectorAll('[data-platform-preset]'));

  data.platforms.forEach(function (platform) {
    var label = document.createElement('label');
    label.className = 'platform-option';
    label.dataset.search = (platform.label + ' ' + platform.id).toLowerCase();
    var input = document.createElement('input');
    input.type = 'checkbox';
    input.name = 'platform';
    input.value = platform.id;
    var text = document.createElement('span');
    var name = document.createElement('strong');
    name.textContent = platform.label;
    var detail = document.createElement('small');
    detail.textContent = platform.native ? '原生读取 AGENTS.md' : platform.files.length + ' 个平台入口';
    text.appendChild(name);
    text.appendChild(detail);
    label.appendChild(input);
    label.appendChild(text);
    picker.appendChild(label);
    input.addEventListener('change', update);
  });

  function update() {
    var selected = Array.prototype.map.call(picker.querySelectorAll('input:checked'), function (el) { return el.value; });
    var platforms = data.platforms.filter(function (p) { return selected.indexOf(p.id) !== -1; });
    var files = data.coreFiles.slice();
    platforms.forEach(function (p) { files = files.concat(p.files); });
    files = files.filter(function (file, index) { return files.indexOf(file) === index; });
    Array.prototype.forEach.call(picker.querySelectorAll('.platform-option'), function (label) {
      label.classList.toggle('is-selected', label.querySelector('input').checked);
    });
    var selection = selected.length
      ? '本次启用以下平台：' + selected.join(',') + '。使用 --platforms ' + selected.join(',') + '；已有选择保留，无需再询问平台。'
      : data.defaultSelection;
    var flow = workflow ? workflow.value : '';
    var workflowSelection = flow
      ? '本次采用 ' + (flow === 'git-flow' ? 'Git Flow（develop 基线）' : 'GitHub Flow（main 基线）') + '，安装时使用 --workflow ' + flow + '。先核对项目现有约定；若冲突再说明，不自动建立或修改远端分支。'
      : data.defaultWorkflow;
    prompt.textContent = data.promptTemplate.replace('{{PLATFORM_SELECTION}}', selection).replace('{{WORKFLOW_SELECTION}}', workflowSelection);
    var flag = selected.length ? ' --platforms ' + selected.join(',') : '';
    var workflowFlag = flow ? ' --workflow ' + flow : '';
    var installCommand = 'python "$HOME/multi-agent-collab/install.py" install .' + flag + workflowFlag;
    command.textContent = [
      '# 1. 仅第一次下载时运行；已有套件请核对来源后复用',
      'git clone https://github.com/AiFLYF/multi-agent-collab "$HOME/multi-agent-collab"',
      '',
      selected.length ? '# 2. 切换到目标项目目录，先预览文件与策略' : '# 2. 先选择上方平台；未选择时安装器会询问，脚本调用需明确 --platforms',
      installCommand + ' --dry-run',
      '',
      '# 3. 预览无误后安装；每步核对输出，失败时先处理再继续',
      installCommand,
      'python "$HOME/multi-agent-collab/install.py" doctor .',
    ].join('\n');
    if (workflowNote) workflowNote.textContent = flow
      ? '将生成 --workflow ' + flow + '。安装前仍需核对现有策略；已有约定冲突时先处理，不自动建立远端分支。'
      : '沿用已配置策略；首次安装先确认分支流程。自定义基线与前缀使用安装器参数，查看使用文档。';
    summary.textContent = selected.length
      ? '已选 ' + selected.length + ' 个平台：' + platforms.map(function (p) { return p.label; }).join('、') + '。下方提示词、终端命令和文件清单已同步。'
      : '未选平台：下方提示词会先确认你正在使用的工具，文件清单仅预览通用部分。';
    fileCount.textContent = files.length + ' 项' + (selected.length ? '' : ' · 通用部分');
    var fragment = document.createDocumentFragment();
    files.forEach(function (file) {
      var item = document.createElement('li');
      var type = document.createElement('span');
      type.className = 'install-file-kind';
      type.textContent = file === '.gitignore' ? '忽略标记' : (data.coreFiles.indexOf(file) === -1 ? '平台入口' : (file === 'AGENTS.md' ? '共享入口' : '协作文件'));
      var path = document.createElement('span');
      path.textContent = file;
      item.appendChild(type);
      item.appendChild(path);
      fragment.appendChild(item);
    });
    fileList.replaceChildren(fragment);
    clear.disabled = selected.length === 0;
    if (selectedList) {
      var chips = document.createDocumentFragment();
      platforms.forEach(function (platform) {
        var chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'platform-chip';
        chip.textContent = platform.label + ' ×';
        chip.setAttribute('aria-label', '移除已选平台 ' + platform.label);
        chip.addEventListener('click', function () {
          picker.querySelector('input[value="' + platform.id + '"]').checked = false;
          update();
          search.focus();
        });
        chips.appendChild(chip);
      });
      selectedList.replaceChildren(chips);
      selectedList.hidden = platforms.length === 0;
    }
    presets.forEach(function (button) {
      var ids = button.dataset.platformPreset.split(',');
      button.setAttribute('aria-pressed', String(ids.length === selected.length && ids.every(function (id) { return selected.indexOf(id) !== -1; })));
    });
    filterPlatforms();
  }
  function filterPlatforms() {
    var query = search.value.trim().toLowerCase();
    var count = 0;
    Array.prototype.forEach.call(picker.querySelectorAll('.platform-option'), function (label) {
      label.hidden = label.dataset.search.indexOf(query) === -1;
      if (!label.hidden) count += 1;
    });
    empty.hidden = count > 0;
    if (searchClear) searchClear.disabled = !query;
    if (searchStatus) {
      var hiddenSelected = picker.querySelectorAll('.platform-option[hidden] input:checked').length;
      searchStatus.textContent = (query ? '找到 ' + count + ' / ' + data.platforms.length + ' 个平台' : '显示全部 ' + data.platforms.length + ' 个平台 · 可多选')
        + (hiddenSelected ? '；另有 ' + hiddenSelected + ' 个已选平台被搜索隐藏，选择仍保留' : '');
    }
  }
  search.addEventListener('input', filterPlatforms);
  if (workflow) workflow.addEventListener('change', update);
  if (searchClear) searchClear.addEventListener('click', function () {
    search.value = '';
    filterPlatforms();
    search.focus();
  });
  presets.forEach(function (button) {
    button.addEventListener('click', function () {
      var ids = button.dataset.platformPreset.split(',');
      Array.prototype.forEach.call(picker.querySelectorAll('input'), function (input) { input.checked = ids.indexOf(input.value) !== -1; });
      search.value = '';
      update();
    });
  });
  clear.addEventListener('click', function () {
    Array.prototype.forEach.call(picker.querySelectorAll('input'), function (el) { el.checked = false; });
    update();
    search.value = '';
    filterPlatforms();
    search.focus();
  });
  update();
})();
