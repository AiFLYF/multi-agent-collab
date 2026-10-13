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
    detail.textContent = platform.native ? '共用 AGENTS.md' : platform.files.length + ' 个平台入口';
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
    var selection = selected.length
      ? '本次启用以下平台：' + selected.join(',') + '。使用 --platforms ' + selected.join(',') + '；已有选择保留，无需再询问平台。'
      : data.defaultSelection;
    prompt.textContent = data.promptTemplate.replace('{{PLATFORM_SELECTION}}', selection);
    var flag = selected.length ? ' --platforms ' + selected.join(',') : '';
    command.textContent = [
      '# 仅第一次下载时运行；已经存在则核对后复用',
      'git clone https://github.com/AiFLYF/multi-agent-collab "$HOME/multi-agent-collab"',
      '',
      selected.length ? '# 在目标项目目录运行；需要预览可在安装命令末尾加 --dry-run' : '# 在目标项目目录运行，首次会询问平台；脚本执行请明确 --platforms',
      'python "$HOME/multi-agent-collab/install.py" install .' + flag,
      'python "$HOME/multi-agent-collab/install.py" doctor .',
      '',
      '# 追加平台或刷新入口，已有选择保留',
      'python "$HOME/multi-agent-collab/install.py" sync .' + flag
    ].join('\n');
    summary.textContent = selected.length
      ? '已选 ' + platforms.map(function (p) { return p.label; }).join('、') + ' · ' + data.coreFiles.length + ' 个通用文件 + ' + (files.length - data.coreFiles.length) + ' 个平台文件'
      : '未选平台：提示词会先确认你正在使用的平台。终端安装会询问，不会默认全装。';
    fileCount.textContent = files.length + ' 个';
    var fragment = document.createDocumentFragment();
    files.forEach(function (file) {
      var item = document.createElement('li');
      item.textContent = file;
      fragment.appendChild(item);
    });
    fileList.replaceChildren(fragment);
    clear.disabled = selected.length === 0;
  }
  function filterPlatforms() {
    var query = search.value.trim().toLowerCase();
    var count = 0;
    Array.prototype.forEach.call(picker.querySelectorAll('.platform-option'), function (label) {
      label.hidden = label.dataset.search.indexOf(query) === -1;
      if (!label.hidden) count += 1;
    });
    empty.hidden = count > 0;
  }
  search.addEventListener('input', filterPlatforms);
  clear.addEventListener('click', function () {
    Array.prototype.forEach.call(picker.querySelectorAll('input'), function (el) { el.checked = false; });
    update();
    search.value = '';
    filterPlatforms();
    picker.querySelector('input').focus();
  });
  update();
})();
