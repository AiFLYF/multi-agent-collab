帮我把 multi-agent-collab 安装到当前项目，并验证所选平台的新对话入口。
套件仓库：https://github.com/AiFLYF/multi-agent-collab

1. 检查 Python 3.8+ 和 Git，确认当前项目的 Git 根目录并记下绝对路径 TARGET。缺依赖时说明，不自行安装软件或 git init。
2. {{PLATFORM_SELECTION}}
3. 套件默认放在用户主目录的 multi-agent-collab：Windows 为 $HOME\multi-agent-collab，macOS/Linux 为 ~/multi-agent-collab。这里是可供多个项目复用的源码目录，不要 clone 到 TARGET 内。
   目录不存在时执行 git clone https://github.com/AiFLYF/multi-agent-collab "<用户主目录>/multi-agent-collab"。已存在时核对 origin 和 install.py 后复用；来源不符或有本地修改时保留现场并说明，不覆盖、不删除、不重置。只有确认工作区干净且配置了上游后，才用 git pull --ff-only 更新。
4. 使用绝对路径运行 python "<套件目录>/install.py" install "<TARGET>" --platforms <已选名称> --dry-run。核对文件清单后执行同一条命令去掉 --dry-run；不要默认 all。只选原生平台时不生成其它平台配置。
5. 安装器负责把本机协作路径写入 .git/info/exclude。若已有规则被 Git 跟踪或忽略无法生效，报告具体文件和原因，不自动取消跟踪、不改写项目 .gitignore、不绕过检查。
6. 根据项目的真实配置填写生成的 AGENTS.md 第 6 节；选了 Claude 再填写 CLAUDE.md 速览。保留已有定制、会话、日志和备份。若改过总章，再运行 python "<套件目录>/install.py" sync "<TARGET>" 刷新已选入口。
7. 执行 python "<套件目录>/install.py" doctor "<TARGET>"，并核对 git status --short。汇报套件复用路径、已选平台、生成文件和体检结果；解释 WARN，不能把未验证项说成成功。
8. 告诉我新开对话验证：让 agent 读取 .agents/START_HERE.md，报告当前分支、看板和 inbox 的实际结果。之后增加平台用 sync "<TARGET>" --platforms <新增名称>，无需重复 clone；旧对话需要主动重新读取规则。

执行安装范围内可恢复的修复即可；遇到需要改变现有项目规则或 Git 跟踪状态的冲突，再说明具体情况让我决定。
