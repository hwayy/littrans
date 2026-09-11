# Claude Code 宿主试运行记录（2026-09-11）

本记录对应分支 `agent/littrans-claude-adapter-4501aa` 在 `21cf17a` 之上的工作区改动（Claude 宿主适配、模型配置外置、布局检测器必需化）。试运行样本为 L. C. Evans, *An Introduction to Stochastic Differential Equations*（AMS 2013），PDF 第 10–12 页（书页 1–3，第 1 章开篇）。项目工作区位于 `D:\Repos\Research\Literature\evans_sde_translate\project`，不入库。协调宿主为 Claude Code 桌面版（Code 标签页，Opus 5 会话，`CLAUDECODE=1`），写手与三镜审校均为通过 Agent 工具派生的 Sonnet 子代理。

**结论：从 `project init` 到 `render` 的不带外审流程在 Claude 宿主上全程跑通；`workflow next` 自动识别 `host: claude`、`limit: 3`，packet 携带 `model: sonnet` / `reasoning_effort: high`；一个批次（51 单元、47 原图资产）经源保真核验、翻译、三镜审校、修订、闭包复审、机器批准并渲染。以上均不是人类批准。**

## 软件回归

| 检查 | 结果 |
| --- | --- |
| `claude plugin validate plugins/literature-translation` 与 `claude plugin validate .` | 通过（`claude 2.1.268`）；`--plugin-dir` 清单仍为 7 技能 + 7 代理 |
| pytest（主 `.venv`，Python 3.13，含真实布局检测器） | 全部通过（后台运行，退出码 0） |
| Ruff / Mypy | 通过；Mypy 同时在 1.19.1 与 2.3.1 下无报错（2.3.1 曾暴露 `fidelity.py`、`math_review.py` 两处既有类型问题，已修） |
| `validate_release.py` | 通过，新增校验 `.claude-plugin` 清单/市场、审校代理 `tools` 限制 |
| `build_distribution.py` | ZIP 含 `.claude-plugin/plugin.json`、`profiles/host-models.yaml`、`layout_runtime.py`；wheel 含 `littrans/profiles/host-models.yaml` |
| `doctor` | `layout_runtime.ok = true`（MinerU 3.4.5，PP-DocLayoutV2） |

## 试运行步骤与观察

1. **准备**：`source probe --pages 10-12`，按 `document-structure.md` 目视三页原图后填写 `context/source-structure.json`（status `reviewed`）。`source prepare` 真实调用隔离布局检测器（3 页 5.7 s，`layout_status: ok`），得到 45 单元、63 区域。
2. **源保真核验**：共 5 轮 `source import-review`（首轮 `regions` 覆盖：删除 6 个装饰性横线/项目符号区域，把碎裂为 5 段的公式 (3) 合并为一个显示公式区域；第二轮 `units` 覆盖：标题/图注/列表项类型、公式编号 `(ODE)/(SDE)/(1)–(5)`、图注与正文拆分、两个项目列表拆分、页眉/DOI 设为 `render_policy: omit`；第三轮批准；随后为第 10 页泄入段落的页码 `1` 再做一轮拆分并批准；最后因 QA 规则变更重新准备 11–12 页并批准）。`source verify --pages 10-12` 通过。
3. **翻译**：`batch create` 得 1 个批次；`workflow next` 返回 `host: claude`；`workflow packet --stage translate` 后派生 Sonnet 写手，查看全部 50 张必需原图，提交并通过确定性 QA。
4. **三镜审校**：三个只读 Sonnet 审校子代理并行运行，`review import-set` 导入（technical 为空结果）。fidelity 报 1 major + 1 minor，chinese-style 报 4 minor + 2 suggestion。
5. **修订**：源修正（页码）与 QA 规则变更共使 6 个单元指纹变化；写手在同一修订中应用 5 条审校意见并重新提交，QA 通过。依赖闭包 39 单元由三镜复审：fidelity/technical 空结果，chinese-style 1 minor + 2 suggestion（非阻断，保留 open）。
6. **批准与渲染**：`approve --level machine` → `machine-reviewed`；`workflow status` 报 `complete: true, reading_complete: true`；`render --batch-id` 与 `--originals-only` 两个版本渲染通过 render QA，浏览器（本地 http 服务）目视检查：合并后的公式 (3) 完整、编号靠右、项目列表为真实列表、行内公式在修复后与正文同行。

子代理用量（宿主报告的 token 数，非计费）：写手 198k + 127k；六次审校 113k、127k、108k、107k、117k、78k。

## 试运行中发现并修复的插件问题

- **审校导入丢失布局证据**：`import_source_review` 重新准备页面时传入空的 `layout.pages`，任何 `regions`/`units` 覆盖都会退化标题识别与分块。已改为按账本 `layout_fingerprint` 复用缓存的检测结果（`fidelity._cached_layout`），并有回归测试。
- **全大写标题的受保护词**：`protected_tokens` 把全大写标题的每个单词当作缩写，迫使写手在译文后附加英文原词。已改为全大写多词文本（标题单词亦然）不再触发缩写模式，并在 `_make_unit` 传入 `heading` 语义。注意该规则参与源单元指纹，已准备页面需重新准备后才生效。
- **审校 packet 的 `[source-only]` 标签**：不可翻译公式单元只显示 `[source-only]`，fidelity 审校据此报出一条错误的 major「遗漏」。已在标签中说明该单元以原图为阅读内容、不构成遗漏；该 issue 在项目中以 `rejected` 结案。
- **行内 mixed-region 强制块级显示**：精确字形导出不可用（本书 PDF 报 `Unsupported intersecting PDF SVG group`）时，行内公式退化为 `mixed-region`，渲染器无视其 `display: false` 而按块级显示，句子被切断。已改为仅 table/code/figure 强制块级，并有测试。

## 遗留

- 本 PDF 全部 44 个行内/显示数学区域的精确字形导出均失败并回退为原始区域截图；阅读无碍，但转写通道仍待运行（`assets_complete: false`，本次未做可选转写）。
- `review external` 未配置也未运行；Claude 宿主下的外审路径（嵌套 `claude -p` 防护、`--actual-model` 信任规则）留待后续修订。
- 三条 chinese-style 非阻断意见保持 open（`b001.unresolved.md` 已列出）；其中一条与首轮意见互相矛盾（「存在解等」），未再起修订。
- 本次未运行 Codex/Cursor 宿主；`--host` 检测与波次上限仅由单元测试覆盖。
- 布局运行时在本机已预先存在，`layout install` 仅验证了幂等分支，未做全新安装实测。

## 后续（2026-09-12）：提取结构增强与检查点

针对 `b001.bilingual.html` 的目视意见（标题截断并成为父节点、插图与图注混入段落、`where … and …` 显示行被挤成一行、粗体向量 **b**/**B** 未识别为公式、行内公式截入引号/连字符且上边界过大），在插件层做了通用性修正，并在同一 PDF 上以全新项目 `project-v2` 重新提取第 10–12 页（未做任何人工覆盖）：

| 项目 | 首轮提取（人工 5 轮覆盖前） | 增强后自动提取 |
| --- | --- | --- |
| 标题 | `1.1. … DIFFERENTIAL` / `EQUATIONS` 两个单元，且成为后续段落 parent | 一个 heading 单元；heading 不再拥有后续正文 |
| 插图 | 图为 paragraph，图注为 paragraph 并与正文合并 | figure 单元 + caption 单元同组，渲染为 `<figure>` |
| 显示行 | `where B… and ξ…` 合并成一段 | `where` / 显示行 / `and` / 显示行 四个单元 |
| 项目列表 | 三个项目合并为一段，圆点成为公式资产 | 8 个 list_item 单元，圆点不再是资产 |
| 公式 (3) | 碎为 5 段 | 一个显示公式并绑定编号 3；尾随短语 `for all times t > 0.` 回到正文 |
| 方程标签 | 仅数字编号 | `(ODE)`、`(SDE)` 也绑定为 equation_number |
| 精确字形导出 | 0/44（空裁剪组导致全部回退原始截图） | 45/51（余 6 个为图形与装饰性横线） |
| 行内裁剪 | 含引号、连字符，上边界含上一行 | 仅自有字形墨迹；引号、连字符、句末标点留在正文 |
| 页码/装饰线 | 页码泄入段落；横线成为正文段落 | 页码分离为 omitted note；细长横线 omitted |

评审包 `boundary_diagnostics` 与 `grouping_pending` 均为空。新增 `source render` 生成 `output/source-pNNNN-pNNNN.html` 作为翻译前的人工检查点（已在浏览器目视：标题层级、图+图注、列表、显示行、行内公式同行）。

测试耗时：本机装有布局检测器时原先每个未打桩的 `prepare_source` 都会启动 torch 子进程并哈希 205 MB 权重，全套超过 20 分钟；现在 conftest 默认把检测器桩为不可用（`@pytest.mark.layout_runtime` 可选择真实运行），`prepared_project` 与 `_make_project` 改为每会话构建一次再复制，全套 1 分 49 秒（Python 3.13，本机）。
