# LitTrans 0.8.1 OpenCode 适配验证

日期：2026-09-29。分支：`dev/0.8`。修复基线：`48929e4`。
宿主：本机 OpenCode `2.0.6`。Python：仓库 `.venv`，3.13.14。

## 修复内容

- 生成 OpenCode 2.x `permissions:`，避免旧式权限字段导致模型设置被忽略。
  只读角色拒绝 edit 和 shell；所有执行角色拒绝再次派发子智能体。
- 将 `project.yaml` 的 OpenCode 模型及思考强度写入原生角色的 `provider/model#variant`。
  生成报告列出各角色的选择器；冲突 variant、缺少 provider 或只有 effort 时拒绝生成。
- OpenCode 的五类默认策略与 Codex 一致，模型加 `openai/` 前缀。
  翻译、转写为 Luna max；译文审阅、资产核验、源文核对为 Sol high。
  探察者和术语研究者没有单独策略，继续继承主会话模型。
- 以绝对路径传入的任务交接文件为入口，按交接文件定位角色快照及项目根目录。
  生成内容不写入机器绝对路径，仍可跨机器同步。
- 更新新会话模型选择、后台任务等待、配置更新与迁移说明。
  现有项目的空策略和手工编辑保留，不自动采用新默认值。

## 确定性检查

`scripts/check.ps1` 完成，退出码为 0：

- 发布元数据、资源链接与 Schema 校验通过，四份插件清单和 Python 包均为 0.8.1。
- Ruff 通过；Mypy 对 40 个源码文件检查通过。
- **1272 passed，2 skipped**，耗时 268.14 秒。
- `doctor` 通过，本机布局运行时可用。

跳过项依赖本机私有 PDF 样本。本轮没有将跳过项视为通过。
新增 16 项测试覆盖默认模型一致性、V2 权限与模型共存、路径锚点、显式留空、
模型策略变更、非法配置拒绝、生成幂等性及用户编辑冲突保护。
完整日志：`tmp/v081-release-checks.log`。

## 构建和升级

`scripts/build_distribution.py` 生成 wheel、插件 ZIP 和摘要清单。
wheel 安装到隔离的 `tmp/v081-wheel-smoke`，确认实际从该目录导入 0.8.1，
四个技能和七份角色资源完整，并成功生成默认原生模型配置。
ZIP 解压后运行其自身 launcher 的 `doctor`，退出码为 0。

将上一轮 0.8 开发版的原始生成配置复制到独立项目后，0.8.1 可无冲突更新旧权限规则，
原有空模型策略保持为空，第二次检查无待更新文件。未修改上一轮测试项目。

产物和证据：

- `tmp/v081-distribution/build-manifest.json`：产物及各打包文件的 SHA-256。
- `tmp/v081-wheel-smoke.json`、`tmp/v081-zip-doctor.json`：隔离安装验证。
- `tmp/v081-upgrade-smoke.json`：旧配置迁移及幂等性。

## 实际宿主验证

使用独立两页合成项目 `tmp/v081-opencode/project`，直接运行修复后的生成器。
主会话显式选择 `deepseek/deepseek-flash#max`。测试期间不手工修补原生角色或模型策略。
源文批准来自合成夹具，不代表真实文献审核。

协调会话为 `ses_f170a0a27ffeKVQBrcOnF8szf7`。原生子会话结果如下：

| 任务 | 子会话 | 会话和助手消息模型 | 结果 |
| --- | --- | --- | --- |
| 翻译 | `ses_f1707d7a9ffePu0qp1eoQstHJm` | `openai/gpt-6-luna#max` | 写入两条译文，接收时 QA 通过 |
| 忠实度审阅 | `ses_f17053258ffeUQmph72j8oYlgI` | `openai/gpt-6-sol#high` | 0 个问题，已接收 |
| 技术正确性审阅 | `ses_f17017342ffe9n3ktGmE7ujp4P` | `openai/gpt-6-sol#high` | 0 个问题，已接收 |
| 中文表达审阅 | `ses_f1700a675ffeKuVMbYHoK9a3Mu` | `openai/gpt-6-sol#high` | 0 个问题，已接收 |
| 未设置模型的探察者 | `ses_f16ff72b1ffe3KN32yQMhh3xdv` | `deepseek/deepseek-flash#max` | 正确继承主模型，读取项目角色指引 |

每个子会话的模型均通过宿主会话和全部助手消息记录核验，不仅检查配置文本。
三个审阅者使用独立子会话，返回空 JSONL 后由协调者保存、接收。
审阅覆盖完整，无过期证据或未覆盖单元。未执行人工批准或最终渲染。

翻译和三个审阅子会话均读取任务中的角色快照和两张原始页面图像，没有角色路径读取失败。
审阅者没有写入、shell 或子智能体派发工具可用。它们使用的 Code Mode 仅用于检查可用能力，
没有绕过只读边界。探察者的角色文件定位也成功。

有一次宿主 WebSocket 短暂异常，随后恢复；未更换模型或修改生成配置。
PowerShell 导出日志遇到编码问题后，改用 Python 子进程保存原始字节，恢复有效 JSON 证据。

详细证据：`tmp/v081-opencode/native-report.md`、
`tmp/v081-opencode/receipts/model-evidence.json`、
`tmp/v081-opencode/receipts/review-status.txt` 和各子会话导出。

## 范围

本轮验证 OpenCode 2.x 配置、模型选择和小样本任务流程。
不据此声称复杂资产、长文献或跨全部宿主的生产质量验收通过。
测试日志、合成 PDF 和生成项目仅存于 Git 忽略的 `tmp/`。
