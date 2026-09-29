# LitTrans 0.8.2-dev.2 Claude Code 验收

## 环境和范围

日期：2026-09-29。分支：`dev/0.8`。基线：`aa9b3dd`（0.8.2-dev.1）。
本机 Claude Code 为 2.1.284，Python 为仓库虚拟环境的 3.13.14。
通过 `--plugin-dir` 加载分支源码，未替换全局插件缓存或修改用户配置。
已有未跟踪的 `.claude/` 保持原样，不纳入提交。

## 修复和观察

九份任务角色原先要求从插件根目录读取角色指引，没有说明任务快照的优先级。
这与任务协议存在指令冲突。现在明确先读取交接目录中的角色快照，
并从交接文件确定项目根路径。无交接文件时，Claude 使用展开后的
`${CLAUDE_PLUGIN_ROOT}`；其他宿主使用相对于角色定义的路径。
外部审阅者的参考文件也采用明确的插件路径。

基线翻译通过交接文件找到了任务目录，本轮没有复现读取错误版本的问题。
路径修改属于消除指令歧义。修改后的三个审阅者实际读取了各自的快照，
无交接文件的探察者也读到了本分支的角色文件。

补充的 Claude 指引包含带插件命名空间的角色名、前台完成等待、
只读工具配置和结果保存流程。`readonly: true` 不是 Claude 的权限控制项，
已有 `tools: ["Read", "Glob", "Grep"]` 才是这些审阅角色的工具限制。
本轮保留共享角色的既有前置配置和其他宿主行为。

初次翻译尝试了未授权的 Bash，之后改用 Read 完成。测试脚本只授权读取、
技能调用和原生派发，未开放写入或 shell。指引已说明受限环境使用 Read。
后续审阅和探察均无权限拒绝。初次 PowerShell 重定向损坏了中文字符，
从 Claude 原始子会话日志恢复的 UTF-8 结果通过接收和 QA。
后续测试用 Python 将 stdout 字节直接写入文件，宿主说明也记录了该方法。

## 真实原生会话

测试文献为两页合成 PDF，包含两个译文单元。源文审核回执来自测试夹具，
不代表生产文献的视觉审核。翻译使用基线角色，三个审阅和直接探察使用修改后的角色。
协调者实际调用了 `literature-translation:translation-coordinator` 技能和原生 Agent 工具。

| 工作 | 原生子智能体 ID | 宿主记录模型 | 结果 |
| --- | --- | --- | --- |
| 翻译 | a4eaac15ca12d6c8e | claude-sonnet-5-5 | 两条译文接收成功，QA 无错误或警告 |
| 忠实度 | a2795cdc55e10628c | claude-sonnet-5-5 | 空问题集，已接收 |
| 技术 | ab1bc721bb225d0c2 | claude-sonnet-5-5 | 空问题集，已接收 |
| 中文表达 | a695e829db0c420b6 | claude-sonnet-5-5 | 空问题集，已接收 |

翻译和三个审阅各自在独立原生上下文中执行，均实际调用 Read 读取两张页面 PNG。
审阅执行记录仅包含 Read 和 Glob；探察者报告只有 Read、Glob、Grep 可调用。
审阅者返回了说明文字和空结果，协调者保留原始日志，将空 JSONL 保存后接收。
机器审阅批准为 `machine-reviewed`，非草稿 Markdown 和双语 HTML 渲染通过。

翻译协调会话为 `59cc214f-ba10-4033-81e0-c78dba9d0852`；审阅协调会话为
`f58cf0fd-d4ae-4e58-9bc1-aa23e40d35e3`。直接探察协调会话为
`ad7ef1aa-c826-44bd-a8de-494f06d8d56d`，未指定子模型，宿主记录为
`claude-opus-5-5`，与父会话一致。模型名来自宿主响应记录，
不独立证明服务端实际模型身份。角色保留 `effort: high`，本轮没有独立验证运行时强度。

## 回归和分发

`scripts/check.ps1` 通过：1275 项通过、2 项跳过，耗时 243.27 秒。
跳过项依赖私有 PDF 样本。Ruff、40 个源码文件的 Mypy、发布元数据和 doctor 均通过。
Claude 和 Codex 插件结构校验通过。中文文档已人工检查排版，本机未提供 autocorrect。
使用基线生成器及当前生成器生成 Codex、OpenCode 配置，结果一致，重复生成无差异。
本轮没有改动两者的生成器、模型默认值或任务接收实现，没有再次调用其真实模型。
共享宿主说明作为生成资源更新，不声称整份资源树与旧版本逐字节相同。

wheel 和插件 ZIP 均完成构建与隔离检查。wheel 版本为 `0.8.2-dev.2`，
含四个协调技能和七份角色资源，OpenCode 默认审阅模型保持不变。
ZIP 的独立 launcher、doctor 和 Claude 插件清单校验通过。
最后一处角色说明调整后重新构建，逐项核对最终 ZIP 与源码字节一致。

本轮没有覆盖复杂资产转写、长文献、外部审阅门禁和最终渲染的浏览器目视验收。
既有其他插件的 MCP 授权提醒不影响本次测试，未更改其连接状态。
没有运行全局插件更新，也没有推送远端。

## 证据位置

临时项目和完整日志保存在 Git 忽略的 `tmp/v082-claude/`：

- `translate-baseline.jsonl`：初次运行，包含已知重定向乱码。
- `translation-native.jsonl`、`translation-received.json`：恢复后的原始译文及接收结果。
- `audits.jsonl`、`audits-received.json`：三视角原生执行与接收结果。
- `direct.jsonl`、`runtime-evidence.json`：路径、模型和工具调用记录。
- `host-regression.json`、`release-checks.log`：宿主回归和完整检查。
- `approval.json`、`render.json`：批准与渲染产物路径。
- `distribution/build-manifest.json`：分发产物及 SHA-256。

指令路径展开依据 [Claude 插件清单参考](https://code.claude.com/docs/en/plugins-reference)。
角色工具和强度字段依据 [Claude 子智能体文档](https://code.claude.com/docs/en/sub-agents)。
