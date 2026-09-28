# LitTrans 0.8 OpenCode 实测

日期：2026-09-29。分支：`dev/0.8`。提交：`48929e4`。版本：`0.8.0-dev.2`。
宿主：本机 OpenCode `v2.0.6`。两组协调会话均使用 `deepseek/deepseek-flash#max`。
测试由真实 OpenCode 会话执行，使用仓库 Python 环境和当前分支源码。

## 结果

两组各自通过 52 项针对性回归，覆盖任务协议、CLI、多宿主、派发和容量限制。
发布校验通过；第一组另通过 1 项发布测试，第二组另通过 6 项发布和打包测试。
本轮未重跑完整测试集。

两组均发现全部 4 个项目技能和 7 个项目角色。两页合成文献的翻译、三视角审阅、
结果接收和重复接收通过。上下文过期及重复使用审阅执行者时，系统正确拒绝操作。

- 第一组：项目角色不设置模型或 effort。原生探察者子会话继承 DeepSeek Flash max。
  翻译及三个审阅任务先通过独立新会话完成，显式选择同一模型。
  随后补测一个原生翻译者及三个原生审阅者，子会话及其助手消息均继承 DeepSeek Flash max，
  页面图像读取成功。补测只返回内容，不写入或接收结果；任务接收验证来自前述独立新会话。
- 第二组：项目角色设置 `model: openai/gpt-6-luna#max`。原始生成配置存在下述兼容问题。
  仅修正临时测试项目后，原生翻译和三个审阅子会话均完成；会话及每条助手消息的模型记录
  均为 Luna max，各子会话调用原生读取工具读取了两张页面图像。

## 发现的问题

### 只读角色的旧式权限配置会使模型设置失效

位置：`plugins/literature-translation/src/littrans/agent_config.py:59`。
生成器为四个只读角色写入以下配置：

```yaml
permission:
  edit: deny
  bash: deny
```

在 OpenCode 2.0.6 中，该配置的权限拒绝规则仍然生效，但同一文件中的 `model:` 被忽略。
受影响的角色为资产核验者、文献探察者、术语研究者和译文审阅者。
因此第二组不能按原始生成配置判定通过：审阅者的指定模型无法解析，会回落到继承行为。

四个独立配置探针证实：调整 `model:` 的前后位置无效；同时保留新旧权限字段也无效。
只使用以下新版配置后，模型与权限均正确解析：

```yaml
permissions:
  - action: edit
    resource: "*"
    effect: deny
  - action: shell
    resource: "*"
    effect: deny
```

该变更仅用于临时测试项目，未修改插件实现。建议适配 OpenCode 2.x 的配置格式，
补充“只读权限与模型同时生效”的宿主测试，并明确旧版 OpenCode 的支持策略。
探针证据：`tmp/opencode-validation-20260929/group2/scratch-agents/probe-summary.txt`。

### 新顶层会话不继承已有会话的模型

第一组曾运行 `opencode run --agent littrans-translator` 而未传入 `--model`。
实际消息使用本机默认的 `opencode/longcat-2.5-preview-free`。
该结果已单独保留，未导入。后续显式传入 DeepSeek Flash max 后完成测试。

这是顶层新会话的行为，不能据此认定原生子智能体继承失败。
独立新会话交接应显式选择主模型；原生子会话则以实际会话记录核验继承。
另观察到一次协调 CLI 在后台任务结束前返回，续接后完成；不将其认定为插件缺陷。

### 嵌套项目中的角色路径有歧义

第一组原生补测中，翻译者和忠实度审阅者最初未在预期路径找到角色文件，随后自行查找，
分别读取测试项目或仓库中的角色指引后继续。生成指令要求相对“工作区根目录”读取
`.littrans/host-agents/opencode/roles/`，但测试项目嵌套在开发仓库中。
这是可恢复的路径定位问题，尚未确定属于宿主根目录判定还是模型执行偏差。
建议在任务派发时明确项目根目录及角色文件路径，并补测独立目录安装。

## 验收边界

测试使用两页纯文本合成 PDF；源文批准来自测试夹具，不是真实源文视觉审核。
本轮未验证复杂资产转写、完整长文献、最终渲染或外部审阅，也未执行人工批准。
读取页面图像的工具记录只能证明调用发生，不能替代对模型视觉判断质量的评估。
三个审阅者均返回无问题；机器审核覆盖通过不等于生产文献质量已经获批。

插件实现和用户配置均未改动，未提交 Git。测试前已有未跟踪的 `.claude/`，保持原状。

## 证据

- 第一组详细报告：`tmp/opencode-validation-20260929/group1/REPORT.md`。
- 第二组详细报告：`tmp/opencode-validation-20260929/group2/report.md`。
- 第二组运行模型与图像读取记录：`tmp/opencode-validation-20260929/group2/sessions/*.json`。
- 第一组原生补测证据：`tmp/opencode-validation-20260929/group1/artifacts/native-dispatch/`。
- 两组主会话日志和提示：`tmp/opencode-validation-20260929/`。

上述详细日志位于 Git 忽略的临时目录；本文件保留可追踪的测试结论。
