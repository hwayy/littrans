# Claude Code 宿主试运行记录（2026-09-12，project-v2 全量翻译）

本记录对应分支 `agent/littrans-claude-adapter-4501aa` 从 `589b9e1` 到本轮提交的工作。样本仍为 L. C. Evans, *An Introduction to Stochastic Differential Equations*（AMS 2013）；项目工作区 `D:\Repos\Research\Literature\evans_sde_translate\project-v2`（v2 提取试验，无人工源覆盖），范围为已核验的 PDF 第 10–13、21–23 页（书页 1–4、12–14）。协调宿主为 Claude Code 桌面版（Opus 5 会话，`CLAUDECODE=1`）；插件通过本地市场**正式安装**到该环境（`claude plugin marketplace add ./` + `claude plugin install literature-translation@littrans`，缓存于 `%USERPROFILE%\.claude\plugins\cache\littrans\literature-translation\0.6.0`，记录提交 `589b9e1`），CLI 从安装副本运行；写手与审校均为 Agent 工具派生的 Sonnet 子代理（`model: sonnet`，packet 记录 `reasoning_effort: high`）。本轮不做公式转写。

**结论：7 页、122 个可读单元、92 个可译单元全部经翻译、确定性 QA、三镜独立审校、修订与闭包复审后达到 `machine-reviewed`（`workflow status` 报 `complete: true, reading_complete: true`，项目状态 `machine-reviewed`，机器复审覆盖 100%）；渲染为三个批次版与一个合并版（`--originals-only`，原图公式）双语 HTML + 中文 Markdown，render QA 全部通过。以上均不是人类批准，也未配置外审。**

## 交付物（不入库，位于 `project-v2/output/`）

| 文件 | 内容 |
| --- | --- |
| `evans-p10-13-21-23.bilingual.html` / `.zh.md` | 合并阅读版（`render --pages 10-13,21-23 --originals-only`），122 单元，render QA 通过 |
| `evans-p10-13-21-23-standalone.bilingual.html` | 同上，282 张原式 SVG/PNG 内嵌为 data URI（4.7 MB），可单文件分享（高清 PDF 原式链接仍需 `original-assets/`） |
| `p0010-p0013-b001.*`、`p0021-p0023-b001.*`、`p0021-p0023-b002.*` | 三个批次的独立渲染（HTML/Markdown/quality/unresolved/render-qa） |
| `*.quality.md` / `*.unresolved.md` | 覆盖 100%，缺译 0，低于门槛 0，开放 blocker/major 0，候选术语/译者不确定项/开放问题均为空 |

## 流程与数据

| 步骤 | 结果 |
| --- | --- |
| `source verify --pages 10-13,21-23` | 通过（第 12 页在本轮因提取修正重新准备并重新核验，见下） |
| 文档简介 / 风格指南 / 术语表 | 本轮补全（27 条批准术语） |
| `batch create --pages 10-13` / `--pages 21-23` | `p0010-p0013-b001`（692 词、70 单元）；`p0021-p0023-b001`（476 词、42 单元）+ `p0021-p0023-b002`（72 词、10 单元，因 60 资产软上限拆分） |
| `workflow next` | `host: claude`，`limit: 3`，三个 translate 任务 `model: sonnet / high` |
| 翻译 | 三个 Sonnet 写手并行，各自查看必需原图（70 / 68 / 12 张），`translation submit` + `qa run` 通过 |
| 三镜审校（第 1 轮） | 每个批次系列一套 `--lens all` packet（不同系列不能同包），6 个只读 Sonnet 审校；`review import-set` 导入 51 条（含 3 个空结果） |
| 修订 | 三个写手各自消化本批次开放问题并做全批次一致性清扫；QA 通过 |
| 三镜审校（第 2 轮）与闭包 | 风格指南修改使全部审校覆盖失效，因此 6 个审校全量重跑（4 空结果，chinese-style 1 minor）；修订后再做 3 个 8 单元闭包复审（2 空结果，1 suggestion 弃用） |
| 批准 | 三个批次 `approve --level machine` → `machine-reviewed` |

审校问题统计（导入 52 条）：

| 批次 | 导入 | 处置 |
| --- | --- | --- |
| p0010-p0013-b001 | fidelity 9、technical 8、chinese-style 11 | 1 major（源提取缺陷，见下）已解决；2 minor 已解决；24 条"列表项缺少 •"驳回（与插件契约冲突，见下）；1 suggestion 弃用 |
| p0021-p0023-b001 | chinese-style 19、technical 1 | 全部解决（半角标点→全角；去掉"两个"这一未在原文出现的量词） |
| p0021-p0023-b002 | chinese-style 4 | 全部解决（`{{asset}}` 两侧多余空格） |

子代理用量（宿主报告的 token 数，非计费）：写手 124k、190k、214k（含修订续用 220k、108k、89k、174k）；审校 12 次 + 闭包 3 次，每次 103k–140k。

## 发现并修复的插件问题（已提交）

| 问题 | 根因 | 修复 |
| --- | --- | --- |
| 粗体全大写陈述标签（`**EXAMPLE 1.**`、`**NOTATION.**`、`**LEMMA.**`、`**IMPORTANT REMARK.**`、`**WARNING ABOUT NOTATION.**`、`**DEFINITIONS.**`）被当作受保护缩写，译文必须原样保留英文，否则 QA `protected-token-missing` | `protected_tokens` 的缩写模式只对整段全大写文本豁免，不识别行首粗体标签 | `run_in_caps_label_words`：新提取不再记录这些词；QA 对已准备单元接受本地化的粗体标签（`**例 1.**`）。写手一度以 `**记号（NOTATION）.**` 绕过，已按修复后规则重提 |
| `p0012-b8-s2`（显示公式 (3) 与同行文字 "for all times t > 0."）`translatable: false`，"对所有时间"整句从译文消失（fidelity major） | 结构重建把公式-only 单元的 `translatable` 标志带入合并后的带文字显示行 | `_make_unit` 对带文字的 `equation` 单元强制可译；第 12 页 `source prepare --replace` 后仅此单元哈希变化，重新核验、`batch refresh`、补译 |
| 空项目 `workflow next` 报 "`--start-at` must not follow `--through`" | 空清单时索引为 -1 | 明确报"请先 `batch create`" |

## 尚未修复的观察（非阻断）

- **风格指南/简介的任何修改都会使全部三镜覆盖失效**（`audit_context_fingerprint` 哈希这两个文件）。本轮因修正"列表项保留 •"的错误指引而全量重审。这是设计使然，但协调者需要在审校前定稿上下文；建议文档明示。
- **列表项契约需要显式告知审校**：`list_item` 译文不含 `•`（QA `target-structural-markup` 拒绝），渲染器负责标记；本轮三镜各报 8 条同类"缺陷"。已把规则写入项目风格指南；插件的审校 packet 说明可考虑内置这句话。
- **单批次渲染页眉的翻译状态取自项目全局状态**（`config.status`），批次已 `machine-reviewed` 而页眉显示 `qa-passed`（当另一批次仍在修订时）。`*.quality.md` 中的 "QA reports / QA errors/warnings" 亦为项目全局计数。
- 默认（非 `--originals-only`）渲染在每个行内原式后附 "转写未完成／待核验 查看原式"，未做转写的阅读版噪声很大；`--originals-only` 正是为此场景准备，建议 `finalize` 技能对"不转写"项目默认推荐它。
- 深色配色下原式图片为白底方块（SVG 无透明背景处理），浅色下正常。
- `render --batch-ids` 拒绝跨批次系列（同 audit packet），合并版需用 `--pages`。
- 图形单元的"只有数学记号"声明必须用 `language_present: false` + `notes`；写手起初在 companion `target_text` 里编造了一句描述以通过 `asset-language-untranslated`，协调者据 packet 说明要求改回。packet 中这条规则已存在，但 QA 错误文案可以直接指向它。
- 写手对 `{{asset}}` 两侧空格、半角标点的习惯不一致（同一模型不同任务）；风格指南本轮已补充明确规则。

## 操作要点（Claude 宿主）

- 管道输出必须 `PYTHONIOENCODING=utf-8`（GBK 控制台遇到中文/• 会让 `emit` 抛错，尽管状态已写入）；Python 在 Windows 写出的 id 列表带 `\r`，循环前需 `tr -d '\r'`。
- `review import-set` 会把审校自报的 `issue_id` 规范化为 `audit-<hash>`，`review resolve` 需用规范 id。
- translate packet 不携带开放问题与当前译文；修订时把写手自己的 JSONL 与导出的开放问题一并交给它。
- `claude plugin update` 在版本号不变时为 no-op；开发中可直接把变更文件复制进缓存副本（launcher 直接从 `src/` 运行），正式更新用卸载/重装。
