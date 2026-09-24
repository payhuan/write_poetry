# Day 3：单轮候选与文学评价

新 Search 在 `context.planned_unit` 非空时，单句候选除 id/text/line_index 外还必须提交
`expression_path`、`semantic_increment`、`risks` 和完整 `semantic_lock`。直接调用旧式单轮且未传 poem_plan 时仍可
读取旧三字段候选。律诗颔联、颈联不使用逐句生成入口，改用[完整句／联生成](unit-generation.md)；其中采用
`poetry-unit-4` 使用 `literary-6`，强制逐字位与逐结构位双层对仗评价并分别打分；律诗颔联承接、颈联转向职责随规划绑定到评分请求。对仗权重为 .25，状态分数上限影响排名，但不直接硬淘汰候选。

单句候选的 `semantic_lock` 字段类型与整联相同，见[完整句／联生成](unit-generation.md)。特别注意，候选锁 `semantic_lock.protected_meaning` 是非空字符串，PoemPlan 的 `protected_meaning` 则是字符串数组；校验器不会自动转换类型。

运行 Skill 的 agent 充当 LLM，不依赖外部模型 API。Python 处理导出上下文、格式校验、去重、规则过滤、评分校验和排序；只完成一个句位的一轮处理。`export` 和 `filter` 不修改状态，`finish` 一次性记录候选历史。正文、规划、revision、全诗 checks、quality_score 和 status 保持原值。

内容扩展：request.allusions / required_fragments 已接入独立内容计划和过滤，详见 [内容安排与履行](round-content.md)。新增 `plan`、`review-content` CLI；export 可传 content_plan，锁定 exact 完整句的 filter 可省略候选输入。本文原有分类 category/passed 和 counts.passed 仍表示纯格律结论；进入文学评分、retained 和推荐还必须通过本句必需内容。内容不加入引擎 checks。更新后的生成上下文带两个数组及独立计划，使用旧导出文件时应从原状态重新 export；旧状态及已保存历史仍可读取，不补写空数组。

## 前置检查与归属

已核对 assessment_binding.py、ConstraintEngine、PoemState 的实际接口，并运行原有 51 项测试：规则结果区分 candidate/poem，绑定 ID、句位、原始文本摘要和创作上下文；set_assessment/evaluate_candidate 都校验对象及上下文。相同 revision 但正文、韵律、约束、模板或规划不同的分支不能交叉导入结果。

沿用引擎的内容绑定语义：上下文完全相同的状态副本表示同一个创作依据，不新增持久化的分支身份系统；每次 export 仍生成不同 batch_id，各批次模型输出不能混用。若需要把内容完全相同的 fork 也当作独立身份隔离，须后续扩展 PoemState 的分支标识及引擎绑定，不能把现有内容摘要称作分支 UUID。

生成批次有随机 UUID 十六进制 batch_id，导出后保持不变；context_sha256 复用引擎创作上下文摘要，generation_sha256 还覆盖 revision、句位、完整生成上下文、预算和 batch_id。最终评分另绑定 filter_sha256 和 rubric_version。恢复时重新构建上下文进行完整比较，正文或规划变化以及预算变化均拒绝旧批次。候选登记不改变创作上下文。摘要用于防误用，不是外部文件的真实性签名。

## Python 接口

脚本目录加入导入路径后：

```python
from constraint_engine import ConstraintEngine
from poetry_round import export_context, filter_candidates, validate_scores, finish_round

context = export_context(state, line_index=2)
# agent 阅读 context 并提供 candidate_json
engine = ConstraintEngine()
filtered = filter_candidates(state, context, candidate_json, engine=engine)
# agent 阅读 filtered.context（创作依据）与 filtered.score_request（候选及评分标准），只评价其中列出的候选
validate_scores(filtered, score_json)  # 可选：在最终写入之前单独校验、修正格式
report = finish_round(state, filtered, score_json, engine=engine)
```

export_context 返回 ready 或 planning_required；缺模板和非法句位抛 RoundError。句位允许诗体内任一合法位置，包括已有句的替代候选，与现有引擎一致；不会填充缺失正文。韵脚句未选韵部返回 RHYME_GROUP_UNSELECTED，不猜选；非韵脚句可继续，未选韵部仍为 null。律诗上下文列出当前联名、结构规划中同名联的 intent、对应句位及已有文本；无规划 intent 时为空数组，不捏造作用。

filter_candidates 整批校验后才检查规则，复用一个引擎和一个状态快照，检查时不追加正文。`filtered.context` 是权威创作上下文；派生的 `score_request`／`content_request` 仅含候选、要求与上下文摘要，不重复内嵌整份上下文。finish_round 重新过滤并比对保存的中间结果，防止把改写的规则输出当作通过结果；同一 Python 流程可复用同一个 engine/lexicon，CLI 不同进程各加载一次。不会每个候选重新加载韵库。本阶段的候选生成封套不接收 `readings` 或 `duplicate_review`，额外字段会被拒绝。规则过滤产生 `reading_request` 与 `duplicate_request`；控制器分别通过 `review_readings`（CLI `review-reading`）和 `review_duplicates`（CLI `review-duplicate`）导入范围绑定的结果，再交回引擎复检。正文变化后旧证明失效。两类复核都通过后，才会生成内容语义或评分请求。Day 3 旧 filtered 工件须从原状态重新导出和过滤，不能手工补版本号。

## CLI 和完整示例

可从任意目录运行下列命令。相对运行路径按[独立工作区](runtime-workspace.md)解析；内置资源相对于模块只读加载。CLI 拒绝向 Skill 目录写入，也拒绝覆盖已有输出，并自动创建工作区内的输出父目录。

```sh
python scripts/day3_example.py --output-dir examples/day3/run-new
```

这会读取 `examples/day3/fixture.json` 的固定模型输出，使用真实韵库运行一次，生成 state、context、candidates、filtered、scores 和 result 六份 JSON。字串和文学评价都是接口测试夹具，不是创作质量示范。预期结果：2 passed、1 failed、1 unresolved、1 duplicate；2 项完成评分，总分均为 0.1125，按输入顺序排序为 a、b，推荐 a；adopted_id=null，正文仍为空。

拆分运行同一流程：

```sh
python scripts/poetry_round.py export --state examples/day3/run-new/state.json --line 2 --output examples/day3/context-new.json
# agent 按新的上下文标识写入 examples/day3/candidates-new.json
python scripts/poetry_round.py filter --state examples/day3/run-new/state.json --context examples/day3/context-new.json --candidates examples/day3/candidates-new.json --output examples/day3/filtered-new.json
# agent 按 filtered-new.json 中 score_request 写入 examples/day3/scores-new.json
python scripts/poetry_round.py finish --state examples/day3/run-new/state.json --filtered examples/day3/filtered-new.json --scores examples/day3/scores-new.json --output examples/day3/result-new.json
```

从其他目录调用时，只需以项目实际位置定位脚本，参数仍使用工作区相对路径，或传绝对路径。result 文件为 `{state: 标准PoemState字典, report: 完整审计报告}`；该 CLI 的 --state 同时接受原始状态或此结果包。独立 PoemState.from_dict 使用包中的 state。一个完整结果包先在同目录临时文件写完，再原子发布，已有目标不覆盖；失败不发布部分 JSON。文件系统须支持同目录硬链接，不支持时明确报 IO 错误。

## 生成提示和 JSON 契约

把 export 输出交给 agent，并使用以下提示：

> 根据给出的主题、情绪、意象、结构规划、已有正文及句位，生成多个语义完整的候选句。逐字平仄要求和押韵位置以模板快照为准，兼顾用户限制和律诗当前联作用。尽量接近 candidate_count.target，总数必须在 min/max 内。不要逐字拼填，不自动修补已有候选，不猜选韵部或有利读音。只输出契约 JSON；标识照抄，不自行计算摘要。候选保留原始文本。

顶层必须且只能包含如下字段。示例标识由真实 export 提供，不能直接使用尖括号占位值：

```json
{
  "contract_version": "poetry-round-3",
  "batch_id": "<照抄导出值>",
  "context_sha256": "<照抄导出值>",
  "generation_sha256": "<照抄导出值>",
  "attempt": 1,
  "candidates": [
    {"id": "a", "text": "完整候选句", "line_index": 2},
    {"id": "b", "text": "另一完整句", "line_index": 2}
  ]
}
```

id 是批次内唯一非空字符串，不修剪或改写；line_index 必须是当前句位整数，布尔值不算整数。文本必须是字符串且去除约定外围空白/标点后非空；候选对象不接受其他字段。0 项、超过 candidates_per_round（去重前计数）、重复 ID、错句位或类型错误导致整批 CANDIDATE_FORMAT_ERROR/CANDIDATE_BUDGET；不先处理正确的半批。错误 details 保留错误位置、原值和原因；CLI stderr 为结构化 JSON，退出码 2。JSON 文本解析拒绝重复对象键、NaN、Infinity 和溢出数。

去重只去除外围 Unicode 空白和 text_normalizer.IGNORED 明确列出的标点，保留所有内部文字、标点、空白、简繁和 Unicode 码点。不把“甲乙，丙丁戊”与“甲乙丙丁戊”合并。保留最早项，其他项 category=duplicate、duplicate_of=最早 ID，并保留原文、输入序号、比较文本及原因。去重不修改送入引擎的原文。多句、错误字数或非法字符由规则引擎报告 failed，与 JSON 格式错误分开。

## 文学评分规范：literary-1

只评价 score_request.candidates。failed、unresolved、duplicate 均不能出现于 scores；评分输入不能包含 checks、valid 或自报 total。额外字段会被拒绝。

| 维度键 | 含义 | 权重 |
| --- | --- | --- |
| theme | 与主题及情绪相符，不要求重复主题词 | 0.25 |
| naturalness | 句意、语法和措辞自然，避免凑韵与堆砌 | 0.25 |
| imagery | 意象具体且相互关联，避免无意义罗列 | 0.20 |
| continuity | 与已有正文、视角、当前句/联意图衔接 | 0.15 |
| expression | 表达准确、有余味；不机械奖励生僻华丽或“诗眼” | 0.15 |

每维使用 0–1 的有限数，固定锚点：0=不符合，0.25=明显薄弱，0.5=基本成立但有明显不足，0.75=表现良好而有局部不足，1=在当前上下文中充分且准确。可用中间值；无依据时不假装完成评价。程序使用十进制权重求和后输出数值 total；按 total 降序、原始 input_index 升序稳定排序。评分 JSON 数组顺序不影响同分顺序。分数用于本轮比较，不代表跨主题的客观质量认证。

评分提示：

> 只评价程序列出的规则通过候选。按 literary-2 逐维评价，给出简短、具体的总体理由，说明主要优点或不足。重视自然、准确与上下文衔接，不因词藻多或机械加入诗眼而加分。无法评价的项明确为 null。程序会在加权基础分之后扣重字分：第一组免费，第二组起每组 -0.20；不要尝试用评价理由抵消。律诗可补充语义或句法对仗观察，但不能声称程序完成语义对仗。照抄批次、上下文、过滤摘要和尺度版本；只输出 JSON，不修改规则结果，不计算总分。

```json
{
  "contract_version": "poetry-round-3",
  "batch_id": "<照抄值>",
  "context_sha256": "<照抄值>",
  "generation_sha256": "<照抄值>",
  "filter_sha256": "<照抄score_request值>",
  "rubric_version": "literary-1",
  "attempt": 1,
  "scores": [
    {"id": "a", "evaluation": {
      "dimensions": {"theme": 0.75, "naturalness": 0.75, "imagery": 0.5, "continuity": 0.75, "expression": 0.5},
      "reason": "主题与衔接基本自然，意象和结句仍较常见。"
    }},
    {"id": "b", "evaluation": null}
  ]
}
```

每个 passed ID 必须且只能出现一次；缺项、错 ID、重复评分、错摘要/批次/版本、缺维度、布尔值、字符串分数、越界及非有限数均拒绝整批评分。evaluation 非 null 时五维和非空 reason 必填（理由最多 500 字符）；律诗可多一个非空 parallelism_observation（最多 500 字符），绝句不接受该字段。全部评分缺失可省略 finish 的 --scores；程序将所有 passed 评分设为 null。显式 null 表示未评价，不能默认零分或高分。有效低分包括零分仍算完成评价，不擅设淘汰阈值。

## 历史、失败与重试

状态仍为 0.3.0，无需迁移已有文件。候选历史 ID 为 `batch_id:模型局部ID`，从规则检测起就使用这个完整 ID，结果归属不在后续重写。parent_path 为 `round:batch_id:generation_sha256`。history 保留原文、句位、revision、原始检测、quality_score 和 decision；reason 是可解析 JSON 字符串，保存分类、规则原因、重复依据、完整五维评价和理由、评分缺失及 adopted=false。report 另保存导出上下文、原始模型候选/评分、输入顺序、归一比较文本和完整过滤结果。持久化时保留整个结果包，避免丢弃批次审计依据。

| 条件 | 历史 decision | 结果行为 |
| --- | --- | --- |
| passed 且完成评分 | retained | 进入稳定排序，可被推荐 |
| passed 未评分 | pending | 分数 null，不进入排序 |
| unresolved | pending | 检测 valid=null，不进入评分或排序 |
| failed 或 duplicate | rejected | 保留原因，不进入评分或排序 |

retained 只表示本轮保留可供选择，不扩展路径，不按 beam_width 截断，也不是最终采用。推荐项仍 adopted_id=null。实际采用须后续显式调用 append_line/replace_line，随后重新检测；本轮不产生全诗通过结果。有内容要求时，格律 passed 而内容 violated 的项为 rejected，内容 unknown 的必需项使候选保持 pending；仅当前应履行的必需内容阻断，后续要求 pending 不误拒绝当前候选。

结果结构包含 status、code、counts、missing_scores、records、ranking、recommended_id、adopted_id。ROUND_EVALUATED/completed 只表示本轮评价完成，不代表全诗完成。部分 passed 未评分为 SCORES_INCOMPLETE/partial；全部未评分为 SCORING_MISSING/incomplete；全部规则失败为 ALL_CANDIDATES_FAILED/unavailable；全部未知为 ALL_CANDIDATES_UNRESOLVED/unavailable；失败与未知混合为 NO_PASSED_CANDIDATES/unavailable。无可排序项时推荐为 null。重复项不改变对剩余唯一项的失败分类。

最终写入前统一验证过滤依据和整个评分包，再通过 record_candidate_batch 在副本上调用普通 candidate API，全部成功后一次提交；ID 冲突等后段错误也不影响原状态。已记录批次再次提交报 BATCH_ALREADY_RECORDED，不重复写入。显式 null 的批次一旦 finish 便完成本次记录；如希望补齐评分，应在 finish 前修正，或由调用者明确开始新批次，不偷偷覆盖历史。

生成和评分各允许首次输出加两次格式修正：attempt=1、2、3。agent 保留本批次各阶段的计数和错误反馈，不重置计数，不通过新 batch_id 规避预算；Python 拒绝范围外 attempt，CLI 不调用模型、不自动重试。第三次仍格式错误即停止并报告最后的结构化错误与“格式重试已耗尽”；没有任何无限循环。重试只针对 JSON/契约错误，格律 failed/unresolved、缺规划和旧批次不是格式重试理由；返回原因交给后续明确决策。本阶段没有自动搜索控制器，也不执行 max_rounds 或 max_backtracks。

## 验证

`python -m unittest discover -s scripts -p 'test_*.py'` 运行原有及 Day 3 的确定性测试。新增测试使用可控模型输出和小型韵库覆盖 16 种用户诗格、候选分类、评分隔离、去重、预算、严格评分、同分顺序、上下文失效、跨批次、原子写入及正文不变；迁移测试复制整个项目到项目内临时目录，从其他工作目录执行 export/filter/finish，实际加载复制后的模板与真实韵库。

本次验证：75 项 unittest 通过（原有 51 项、新增 24 项），Skill 结构校验通过；固定示例输出位于 examples/day3/run/result.json。模型夹具验证流程，不证明真实生成的文学质量。
