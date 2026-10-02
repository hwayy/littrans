# 统一段落模型 v8 验收记录

验收日期：2026-10-02。插件版本：`0.9.2-dev.4`。
数据格式：项目 v8、SourceUnit v4、TranslationRecord v3。
当前行为见[统一段落模型](../plugins/literature-translation/references/paragraph-model.md)。

## 实现范围

标题、编号条目与正文统一为 `kind=paragraph`。大纲层级、标签、排版，以及段落、条目、
语义容器和章节归属分别记录。编号与大纲层级独立，任意 Unicode 标签按原文保存。
固定标签由渲染器恢复，可翻译标签通过分片 ID 提交译文。程序不生成编号或额外圆点。

两条提取路径、正式覆盖、结构 sidecar 和审核合同支持新字段。版面与书签只提出候选；
源文审核必须确认全部段首。结构疑问或过期的审核绑定会阻止页面进入翻译及旧批次提交。
批次、上下文、指纹、失效范围、QA 和输出使用相同的显式关系。

`project upgrade OLD NEW [--dry-run]` 支持 v7 到 v8 的独立工作区升级，先暂存并校验，
再提交新目录。保留旧工作区、原始译文和历史证据；旧批准不能授予新结构。
技能、角色、任务合同、Schema、CLI 参考、结构指导和 CHANGELOG 已同步。

## 自动验证

完整测试：**1538 通过，7 跳过**，耗时 264.38 秒。
日志：`tmp/paragraph-final3.log`。使用仓库虚拟环境及本地源码：

```powershell
. .venv/Scripts/Activate.ps1
$env:PYTHONPATH=(Resolve-Path plugins/literature-translation/src).Path
python -m pytest plugins/literature-translation/tests -o addopts='--strict-markers' -q --tb=short --basetemp=tmp/paragraph-final3 -p no:cacheprovider
```

新增与更新的测试覆盖以下行为：

- `(1)`、罗马数字、`6.1./6.10.`、`a)`、不同项目符号及未预设的 Unicode 标签。
- 无编号、带编号、只有标签的标题，超过六级的大纲、层级跳跃及跨页标题。
- 普通编号段落、侧栏标题、年份、交叉引用和数学括号不因外观自动进入大纲。
- 标签范围与字形证据、完整原文分区、标签译文映射，以及字面标签与明确行内格式。
- 悬挂缩进、多段条目、嵌套、项内公式、跨页续接及浮动素材穿插。
- 未确认结构阻止翻译、错误归属被拒绝，以及大纲变化后的上下文和证据失效。
- 旧标题层级缺失、旧译文含标签、历史批准归档、失败回退、重复升级和 dry-run 无写入。
- HTML 标题语义、Markdown 层级与文字转义、中英文标签一致、目录和续接标签只显示一次。

其他仓库规定检查：

| 检查 | 结果 |
| --- | --- |
| Ruff | 通过 |
| 严格 mypy | 52 个源码文件通过 |
| `update_configuration_reference.py` | 已重新生成 Schema、配置及 CLI 字段参考 |
| `validate_release.py` | 版本、Schema、指令链接及发布元数据校验通过 |
| `littrans doctor` | 通过；本地源码版本 dev.4，构建标识 `40194cdbd2651862` |
| 分发构建 | wheel、插件 ZIP 和哈希清单生成成功 |
| `git diff --check` | 通过 |

`scripts/check.ps1` 的固定 pytest 临时目录存在权限限制，首次 pip 缓存也不在可写范围。
已将 pip 缓存切换到仓库临时目录，并使用新的 pytest basetemp，逐项执行脚本要求的
全部检查。上述通过结果来自这些独立命令，不能理解为原脚本完整执行成功。
测试跳过项保留各自的本地条件，没有通过修改跳过条件来提高通过数。

## 浏览器验收

通过本地 HTTP 服务在 Codex 浏览器检查代表性合成页面及正式双语渲染器输出。
检查 DOM、可访问性树和实际截图：

- 一级、二级标题使用对应 h1/h2；八级标题具有 `role=heading`、`aria-level=8`。
- 目录保留 1、2、8 的原层级；编号正文没有额外列表外壳。
- 宽的中文标签、Unicode 标签与悬挂缩进没有遮挡正文；501 像素宽视口仍可阅读。
- 标签重复和标签与正文重叠检查均为 0。
- 只有标签的标题在源文侧和目标侧各显示一次，正文为空，目标目录使用标签译文。

合成页面：`tmp/paragraph-browser/acceptance.html`。
正式双语页面：`tmp/paragraph-browser/label-only.html`。
这些结果验证渲染行为，不替代原书的逐页结构审核。

## 真实书籍独立升级验收

使用关联会话的旧工作区，在仓库外创建独立 `book-v8` 工作区，执行实际 CLI dry-run
与升级。原书 PDF、图片和工作区没有加入仓库。新版检查点与源文任务包能够正常生成；
页图像恢复到活动证据目录，旧审核仍仅保存在历史目录。

本次新工作区位置：
`C:/Users/Hwa/.codex/visualizations/2026/10/01/01a0f86d-fc6f-7b81-bc75-3ab3be28fc50/book-v8`。

| 项目 | 结果 |
| --- | --- |
| 原工作区 | 未改动；原单元文件与升级归档的原文件哈希一致 |
| 原文保留 | 4438 个单元的完整原文逐项一致 |
| 旧列表单元 | 检查 118 个 |
| 额外圆点外壳 | 75 → 0 |
| 原书圆点 | 5 → 5 |
| 新工作区未审核单元 | 4438 个；没有继承旧批准 |

圆点结果来自升级后完整原文的渲染保留检查。标签拆分、大纲和内容归属仍需重新确认，
不能将该结果视为整本书的新结构已经审核通过。转换清单位于新工作区的
`derived/paragraph-upgrade.json`，独立验收统计位于同级 `book-upgrade-acceptance.json`。

## 分发产物

产物目录：`tmp/paragraph-distribution-v8`。构建未切换或修改已安装插件。

| 文件 | SHA-256 |
| --- | --- |
| `littrans-0.9.2.dev4-py3-none-any.whl` | `120880605e795ca79513219abc29606ae96743e79e1e7f6ab3d03de5d580e804` |
| `literature-translation-0.9.2-dev.4.zip` | `babe3224dc7319f5346b76917c1e7d719b81d6b33b60cb163ed6d75c86714f4e` |

`build-manifest.json` 记录产物大小和插件源文件哈希，供安装时核对。
