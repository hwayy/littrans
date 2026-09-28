# LitTrans 0.8.2-dev.1 Codex 验收

## 环境和范围

日期：2026-09-29。分支：`dev/0.8`。基线：`5f81599`（0.8.1）。
当前 Codex 桌面会话加载的插件是 0.8.0-dev.2。本轮直接运行分支源码，
并在新 Codex CLI 会话验证生成的项目角色。CLI 版本为 0.158.0-alpha.2.1，
Python 为仓库虚拟环境的 3.13.14。没有改动插件缓存或全局宿主配置。

## 修复

- Codex 原生角色原先从工作区根目录读取指引。现在优先使用任务内快照，
  缺少交接文件时从角色定义文件定位资源，避免嵌套项目路径混淆。
- 使用相对项目路径调用 `task claim/status/receive` 时，交接路径原先也是相对路径。
  现在统一返回绝对路径，和 `task create` 保持一致。
- Codex 指引明确使用 `fork_turns="none"`，避免默认继承协调上下文，
  并按任务配置传入模型与思考强度。三个审阅视角分别使用新上下文。
- 原生角色增加禁止继续派发的指令和 `agents.enabled=false`。
  文档说明宿主可能仍暴露派发工具，不能把该配置当作安全边界。
- 写入受限时，协调者保存工作者的原始结果，再调用领域接收接口。
  成功生成的译文不因保存失败而重新翻译。

## 确定性检查

`scripts/check.ps1` 退出码为 0：1275 项通过，2 项跳过，耗时 246.08 秒。
跳过项依赖私有 PDF 样本。Ruff、40 个源码文件的 Mypy 检查、发布元数据和
`doctor` 均通过。Plugin Creator 的 `validate_plugin.py` 通过。
新增三项回归覆盖相对项目路径，以及项目内和包含工作区两种角色生成方式。

升级测试用 0.8.1 的生成器创建旧 Codex 定义，再运行新生成器。
七个定义升级成功，重复生成无差异。OpenCode 原生角色生成结果与 0.8.1
生成器完全一致。已有 OpenCode 默认模型、权限和用户修改冲突保护测试均通过。
本轮没有再次调用真实 OpenCode 模型；其实际宿主证据见上一版验收记录。

## 真实 Codex 小样本

项目位于 `tmp/v082-codex/project`，含两页合成文献、两个译文单元。
源文批准来自测试夹具，不代表人工或真实文献源文审核。
翻译与三个审阅者均读取任务内角色快照，并实际查看两张原始页面图像。
执行记录核对了以下子会话的模型配置；这些记录不独立证明服务端实际模型身份。

| 工作 | 子会话 | 宿主记录模型 / 强度 | 结果 |
| --- | --- | --- | --- |
| 翻译 | `01a0e914-17b6-7053-a938-caec67bddede` | gpt-6-luna / max | 两条译文接收成功，QA 通过 |
| 忠实度审阅 | `01a0e919-e564-7853-b152-6e0c8faf2949` | gpt-6-sol / high | 空问题集，已接收 |
| 技术审阅 | `01a0e91a-08ba-7312-a048-a2a82e06369d` | gpt-6-sol / high | 空问题集，已接收 |
| 中文审阅 | `01a0e91a-29b5-7af0-b513-ed37062b3b7e` | gpt-6-sol / high | 空问题集，已接收 |

翻译者首次保存结果遭遇文件系统拒绝。它返回完整 JSONL，协调者保存原文后接收，
没有重跑翻译或改动源文。三个审阅者只返回内容，协调者保存空 JSONL 并接收。
机器审阅批准通过，非草稿双语 HTML 和 Markdown 渲染成功。
本轮没有做渲染页面的浏览器目视验收，也没有覆盖复杂资产或长文献。
忠实度审阅者指出夹具将两个分开的段落组合为一个单元；两句内容均完整翻译，
该夹具结构不作为生产源文划分的示范。

## 原生角色发现和限制

新 CLI 会话 `01a0e917-532d-7132-8afc-27685bad6089` 实际通过
`agent_type="littrans-document-scout"` 派发。子会话
`01a0e917-8992-74f1-b4af-66546133a4e0` 的宿主记录确认了该角色。
父子模型均为 gpt-6-astra / medium，符合未设置探察者策略时的宿主默认行为。
子会话读取任务快照，提取第一页文本并返回探察结果，没有写文件或再次派发。

子会话报告仍可见嵌套派发工具。因此只确认原生角色发现、路径读取和遵守指令；
不声称 `agents.enabled=false` 在该宿主强制移除了工具。
原生角色字段依据 [OpenAI 子智能体文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)
核对。普通桌面子智能体使用当前提供的 `collaboration.spawn_agent` 接口。

## 构建和证据

wheel 与 ZIP 均实际构建。wheel 在独立目录安装后核对版本、四个技能、七份角色
和 OpenCode 默认模型；ZIP 使用自身 launcher 执行 `doctor` 通过。
最终文档调整后重新构建和验证，产物 SHA-256 记录在构建清单中。

日志、合成 PDF、任务快照和结果仅保存在 Git 忽略的临时目录：

- `tmp/v082-codex/release-checks.log`：全量检查。
- `tmp/v082-codex/translation-received.json`：译文接收及 QA。
- `tmp/v082-codex/project/.littrans/work/tasks/`：任务和结果。
- `tmp/v082-codex/native-scout.jsonl`：原生角色会话结果。
- `tmp/v082-codex/upgrade-smoke.json`：升级与 OpenCode 基线比较。
- `tmp/v082-codex/approval.json`、`render.json`：批准和渲染。
- `tmp/v082-codex/distribution-final/build-manifest.json`：最终分发产物摘要。

未推送远端。当前聊天仍保留启动时的技能目录；本轮源码测试不等于更新已安装缓存。
