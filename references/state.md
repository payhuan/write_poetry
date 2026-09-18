# 输入与状态

自然语言入口见 [输入解析](input-parsing.md)：LLM 提取语义，`parse_input.py` 校验、归一化并初始化状态。以下是底层 `init_state.py` 接收的规范化 JSON。

```json
{"raw_request":"写一首秋日山寺七绝，清冷一些","theme":"秋日山寺","scene":"山寺","emotion":["清冷"],"characters":[],"time":"秋日","poem_type":"七言绝句","constraints":[]}
```

`raw_request`、`theme` 为必填非空字符串；`scene`、`time` 为字符串；`emotion`、`characters`、`constraints` 为字符串数组。诗体使用四种全称，省略时默认七言绝句。未知字段报错，无法推断的可选信息留空。

PoemState 包含版本、原始结构化需求、缺省决策、诗体结构、模板标识、主题、情绪、意象、结构规划、韵书和韵部、正文、候选历史、约束、评分、检测结果、预算及当前阶段。

通过解析入口创建时，额外包含 `input_parse`，记录解析方式、原文证据及未提供证据的字段。原文保持不变；文本字段去除首尾空白，数组去空项并稳定去重。别名转换为诗体全称，未指定诗体的默认决策记录在 assumptions。

未决定的模板、韵部和文学评分为 null。首句入韵选项优先采用用户明确要求，未指定才为 null；解析后唯一匹配时会自动绑定模板。`structure` 使用 `{"unit":"首联","intent":"写景入题"}` 这样的记录。每次修改诗句，清空过期检测和评分。

规划写入平水韵韵部时可使用规范名 `十一真`、带声部的 `上平声部/十一真`，也可使用省略序号的别名 `真`。别名在全表只有一个匹配项时自动规范化并以 `十一真` 保存；多个匹配项会返回候选并要求消歧，不把多个韵部合并；无匹配项仍由检测阶段报告不可用。该规则服务于 Skill 内部规划 API，用户自然语言不需要提供韵部序号。

预算初值：每轮 10 个候选，保留 3 条路径，最多回溯 3 次、生成 20 轮。Day 3 单轮接收器执行 candidates_per_round（去重前计数）；Day 4 [搜索控制器](poetry-search.md) 消费其余预算，max_rounds 是整个运行的扩展批次总额。Search 运行状态独立于单首 PoemState，须保存完整运行包才能恢复多路径与实际消耗。

Day 5 的 [Polish 控制器](poetry-polish.md) 独立保存审校预算、工作身份、文学评价、内容证据与修改历史，不扩展 PoemState 0.3.0 字段。局部包整体构造一个新 revision，任何验证失败保留 accepted；采用后重新发出全诗审校。PoemState 自身的最低 completed 条件不等于 Day 5 验收，创作工作流必须使用 Polish 的最终检查。只保存 accepted 字典会丢失文学与内容证明，不能用于恢复 Day 5。

## PoemState 模块

request 可选包含 allusions 和 required_fragments，格式见 [内容要求](content-requirements.md)。旧状态省略时消费者按空数组处理；初始化和恢复会校验已提供的字段。它们只保存用户要求，不自动写入正文，且包含在 request 上下文摘要中。

`scripts/poem_state.py` 提供封装状态的 `PoemState` 类，当前版本为 `0.3.0`，仅依赖 Python 标准库。原有 `initialize(request)` 仍返回字典，底层已改用本模块；输入解析输出可以直接恢复为实例。

命令行创建、绑定或推进状态时，相对文件路径位于[独立工作区](runtime-workspace.md)，不写入 Skill 安装目录。Python API 的 `load(path)` / `save(path)` 接受调用者给出的明确路径，调用方负责将运行文件放在工作区。

request.meter 保存用户指定的 template_id、start、first_line_rhymes；全部允许 null。request.input 保存当前文本封套。输入可省略这两项，初始化自动补齐；模式验证要求已保存的 0.3.0 状态包含规范化字段。直接初始化也会检查用户诗格与模板库的兼容性，但自动绑定由 parse_request 或 TemplateLibrary.bind 执行。

用户要求与规划选择分别存放：request.meter 保留明确要求，template_id 和 meter_template 表示当前选择。所有模板绑定及状态恢复都验证兼容性，不能用 set_plan 覆盖用户明确的首句入韵要求。原先未指定的选项由规划补齐，不能反写成用户要求。

```python
from poem_state import PoemState

state = PoemState.create({"raw_request": "写秋日山寺七绝", "theme": "秋日山寺"})
state.set_plan(
    imagery=["秋山", "古寺"],
    structure=[{"unit": "第一句", "intent": "写山寺秋景"}],
)
state.record_candidate("candidate-1", "山寺秋深落叶多", 1, parent_path="root")
state.append_line("山寺秋深落叶多")
branch = state.fork()
branch.replace_line(1, "古寺秋深落叶多")
state.save("draft-1.json")
restored = PoemState.load("draft-1.json")
```

脚本目录须在 Python 导入路径中；示例诗句仅用于演示状态写入，不代表格律通过。

| 方法 | 行为 |
| --- | --- |
| `create(request)` | 校验规范化输入并创建状态 |
| `from_dict(data)` / `to_dict()` | 校验并导入 / 深拷贝导出 JSON 字典 |
| `load(path)` / `save(path)` | UTF-8 JSON 恢复 / 保存新快照，禁止覆盖已有文件 |
| `fork()` | 创建互不影响的状态副本，供后续搜索控制器使用 |
| `set_plan(**changes)` | 更新主题、情绪、意象、结构、模板、韵律上下文或约束 |
| `set_budget(**changes)` | 设置正整数预算；最大回溯次数允许为零 |
| `append_line(text)` / `replace_line(index, text)` | 追加 / 替换诗句，索引从 1 开始 |
| `truncate_lines(keep)` | 仅保留前 keep 句，保留候选历史 |
| `record_candidate(id, text, line_index, parent_path=None)` | 记录唯一候选及所属上下文版本 |
| `evaluate_candidate(id, checks=..., quality_score=None, decision="pending", reason="")` | 记录检测、评分和决策；不能评价旧上下文候选 |
| `record_candidate_batch(records)` | 使用普通登记和评价入口在副本上校验整批，全部成功才一次提交；失败保持原状态 |
| `set_assessment(checks, quality_score=None)` | 保存当前全诗检测和文学评分 |
| `set_status(status)` | 显式推进创作阶段 |

正文和规划变更递增 `revision`，清空全诗 `checks` 和 `quality_score`，阶段回到 planning（无正文）或 drafting（已有正文）。候选历史保留原 revision，其结果不能当作当前上下文的结果。返回字典与分支均为深拷贝；无效操作不会修改原状态。

Day 3 的 `record_candidate_batch(records)` 接收非空数组，每项严格包含 id、text、line_index、parent_path、checks、quality_score、decision、reason（不传 revision，由当前状态确定）。带检测结果的项按 evaluate_candidate 验证归属；空 checks 只允许未评分的 rejected 项，用于去重记录。实现先在独立副本上完成全批验证，再提交原状态；不会留下半批记录。单轮流程将完整评价和处理依据编码到 reason JSON 字符串，并以结果包保存完整上下文和模型输入；格式见 [单轮候选流程](poetry-round.md)。

典故和片段接入后，reason JSON 可包含独立的 content_result（不进入格律 checks）。使用 poetry_round.read_content_fulfillment(state, history_candidate_id) 读取当前有效内容履行；它验证候选身份、原文、revision 和上下文。正文/规划修改后，历史 satisfied 记录保留作审计但读取当前履行会报 STALE_CONTENT_RESULT。模型内容安排保存在结果包 content_plan，不补写 request，也不改变 0.3.0 状态结构；见 [内容安排与履行](round-content.md)。

旧版检查结果格式为 `{"revision": 1, "valid": null, "checks": {"length": {"status": "not_run"}}, "errors": [], "warnings": []}`。新结果还包含 binding_version=1、scope、subject 和 context_sha256；revision、检测对象及上下文必须同时匹配。对象摘要和兼容策略见 [检测结果绑定](constraint-engine.md)。缺少绑定的旧结果只允许作为历史读取保存，不可通过 set_assessment/evaluate_candidate 重新写入或用于新晋级。有效性仅汇总 required=true 的必需项：有 fail 则 false，有 unknown/not_run 则 null，否则 true。required 省略时默认 true，且只能是布尔值；length、pingze、rhyme、reading 不得降级为非必需项。正常 Constraint Engine 结果中的 duplicate 也是必需项：无重字通过，有重字先 unknown，经绑定 AI 审查后仅允许叠字、意群并列或意群递进。全诗通过至少需要 length、pingze、rhyme 均 pass；候选非韵脚句的 rhyme 可为 not_applicable。评分统一为 0–1 有限数或 null。候选 decision 为 pending、retained 或 rejected；新晋级 retained 必须有绑定该候选和当前上下文的通过结果。

阶段依次为 planning → drafting → reviewing → completed；reviewing 也可退回 drafting。审校要求句数齐全，新晋级完成还要求当前版本的全诗绑定检测通过且已选定模板和韵部。状态模块不执行格律检测；使用 [纯规则检测引擎](constraint-engine.md) 获取结果。写入草稿只限制非空文本与总句数，字数和平仄由检测引擎判断。

通过 `TemplateLibrary.bind` 或 `bind_template(template, source_sha256)` 绑定模板后，状态额外包含 `meter_template: {data: 完整模板, source_sha256: 库文件摘要}`。恢复时验证快照结构及其与诗体、template_id、首句入韵选项的一致性。已有快照时不能单独修改 ID 造成冲突，应重新绑定模板。旧版仅有 template_id 的状态仍可读取，但该标识未被库验证，使用前应绑定模板。快照保留模板 constraints，与用户 constraints 分开。

支持从旧 `0.1.0` 状态迁移：补 revision，并清空无版本依据的全诗检测和评分；旧候选历史如不符合新结构会明确报错，不猜测其含义。未知版本、缺失字段和非法状态会拒绝恢复。

`0.2.0` 状态迁移到 `0.3.0` 时补齐 request.meter 和 request.input；保留已有模板选择，不据此推断用户曾明确指定这些选项。数据仍由项目内模块与模板库解析，不依赖开发机器路径。


