# Day 5：全诗审校、局部炼句与最终润色

## 职责与入口

`scripts/poetry_polish.py` 提供 `Polish`，快照与工作封套版本为 `poetry-polish-1`，审校量表为 `whole-poem-review-1`。运行 Skill 的 LLM 阅读工作并提供 JSON；程序不调用模型 API、不编造评分、不重做搜索、不处理图片。

LLM 负责主题、情绪、时间、视角、意象、结构和语言，律诗中间两联的语义及句法对仗，用典及 adapt 语义，修改建议、候选、全诗比较和诗题。程序负责交接、绑定、范围、锁定要求、规则复检、内容契约、事务采纳、预算、持久化和完成条件。语义报告的真伪及文学质量不能由 JSON 校验器证明。

```python
from poetry_polish import Polish, IDENTITY

# search_snapshot 必须是 Search.to_dict() 返回的完整包。
# path_id 由调用者根据 drafts 明确选择，不能默取任意草稿。
polish = Polish.create(search_snapshot, path_id,
                       max_rounds=3, candidates_per_round=3,
                       max_interactions=40, max_format_repairs=2)
job = polish.next_job()
response = {k: job[k] for k in IDENTITY}
response['payload'] = agent_json  # 也可为未经解析的模型 JSON 字符串
polish.submit(response)
polish.save('examples/polish-01.json')
polish = Polish.load('examples/polish-01.json')
polish.result(include_notes=False)
```

交接检查：完整 Search 快照校验通过且 status=ready_for_review；所选 path_id 唯一存在于 drafts；完整 PoemState 为 reviewing；request、诗体、模板快照、韵部、首句入韵和规划均与 Search.initial 一致；当前检查和 final_content_binding 绑定正确；内容安排与证据齐全。重新调用当前规则引擎，并通过 Day 3 `_verify_filter` 重建内容过滤结果及导入语义报告，确认当前正文全部必须项 satisfied。任意字典、仅有 passed 标记、旧对象、缺证据或规则 unknown 均拒绝交接并给出错误；不通过重置状态绕过。

历史候选 quality_score 和路径平均分不进入全诗评分；accepted.quality_score 在开始时清空，最终完成时才记录当前全诗审校总分。旧 Search 保持原样嵌入 source 供追踪。摘要用来防混用、损坏和错误修改，不是签名；快照须来自可信本地运行。

## 工作身份与阶段

每次新工作导出时扣一次模型额度。next_job 返回深拷贝，多次读取不扣费。response 严格包含下表全部身份字段及 payload：

| 字段 | 规则 |
| --- | --- |
| contract_version | poetry-polish-1 |
| run_id / job_id | 当前运行与工作 ID；格式修复保持 job_id |
| path_id | 显式选定的 Day 4 草稿分支 |
| revision | 工作 target 的版本，整数 |
| binding | 原样照抄 `assessment_binding.make_binding(target)` 的四字段对象 |
| work_sha256 | 工作全文摘要，覆盖身份、stage、context，包括正文、安排、参考证据、原稿和修改包 |
| attempt | 照抄当前尝试号 1、2、3 |

binding 复用现有规则摘要；没有第二套正文摘要算法。完整相关上下文放在 job.context：accepted、target、用户 request、模板、韵部、规划、assignments、review、candidate、当前可用内容证据、预算、权重、Search 运行标识与交接摘要。compare/candidate_content 的 target 是候选完整诗，其余是当前接受版本。内容 payload 还保留 Day 3 批次与过滤摘要。所有导入的原始结果、外层工作身份和拒绝信息均在快照中保留。

| stage | LLM payload | 后续 |
| --- | --- | --- |
| review | 下述全诗审校对象 | ready → final_content/final；revise → generate |
| generate | 局部修改包数组或明确需要用户改变要求 | 程序原子构造候选并检查 |
| candidate_content | 原 Day 3 review-content JSON | 全部必须项通过才 compare |
| compare | 候选全诗审校、全维度比较和采用建议 | 采用或保留原稿 |
| final_content | 原 Day 3 review-content JSON | 当前必须内容全部通过才 final |
| final | 独立诗题与可选说明 | 最终程序检查后 completed |

常规路径：review → generate → 规则/exact → candidate_content → compare → 采用或拒绝 → review → final_content → final。无问题直接 review → final_content → final。没有内容要求时自动跳过内容模型工作；exact 由程序检查。每次实际改动后重新审校，compare 中的 candidate_review 不直接冒充采用后的最终审校。

## 全诗审校契约

payload 严格包含以下字段，模型不输出 total。这里用占位文字表示必须照抄的绑定对象，实际文件必须为合法 JSON 值：

```json
{
  "review_version": "whole-poem-review-1",
  "binding": "照抄 target 的完整 binding 对象",
  "revision": 8,
  "dimensions": {
    "theme": {"status":"assessed","score":0.8,"reason":"具体主题评价理由","couplets":[]},
    "naturalness": {"status":"assessed","score":0.8,"reason":"具体语言评价理由","couplets":[]},
    "imagery": {"status":"assessed","score":0.8,"reason":"意象关联及重复情况","couplets":[]},
    "structure": {"status":"assessed","score":0.8,"reason":"推进与结尾评价","couplets":[]},
    "consistency": {"status":"assessed","score":0.8,"reason":"情绪时间视角评价","couplets":[]},
    "expression": {"status":"assessed","score":0.8,"reason":"关键表达评价","couplets":[]},
    "parallelism": {"status":"not_applicable","score":null,"reason":"绝句不要求中间两联对仗","couplets":[]}
  },
  "issues": [],
  "uncertainties": [],
  "recommend_revision": false,
  "conclusion": "ready",
  "user_constraints": []
}
```

上述 user_constraints=[] 只适用于原始 request.constraints 为空。每条原始自由文字约束须按原顺序输出 `{"constraint":"原文","status":"satisfied|violated|unknown","reason":"具体理由","evidence":[{"line_index":1,"quote":"该句真实引文"}]}`。这用于否定要求等语义复核，不能替代结构化 exact 检查。用户锁定字句应在输入提取时保留为 exact；LLM 不得把明确锁定要求解释成可改。

| 维度 | 权重 | 观察内容 |
| --- | --- | --- |
| theme | .20 | 主题契合及表达是否含蓄准确，不计主题词频 |
| naturalness | .20 | 全诗语法、语气、用字自然，是否凑韵 |
| imagery | .15 | 意象协调、关联及有意义/无意义的重复 |
| structure | .15 | 全诗推进、转折与结尾，不强制统一起承转合 |
| consistency | .10 | 情绪、时间、空间视角与叙述主体一致性 |
| expression | .10 | 关键表达的准确性、力度和余味，不计华丽词数 |
| parallelism | .10 | 律诗颔联、颈联的语义和句法对应 |

0=不成立，.25=明显薄弱，.5=基本成立但有明显不足，.75=良好而有局部不足，1=在当前上下文充分且准确；中间值允许。全部适用维度均须给有限 0–1 数值和非空理由。绝句 parallelism 明确 not_applicable/null，从分母排除：`total = Σ(score × weight) / Σ(applicable weight)`，由程序使用十进制分数计算。不适用不等于高分。律诗 parallelism 必须 assessed，并提供两条 couplets：

```json
[
  {"line_indices":[3,4],"semantic":"两句语义对应的具体评价","syntax":"两句句法对应的具体评价"},
  {"line_indices":[5,6],"semantic":"两句语义对应的具体评价","syntax":"两句句法对应的具体评价"}
]
```

每个问题严格为：

```json
{
  "id":"i1",
  "dimension":"expression",
  "locations":[{"line_index":1,"quote":"正文中的原字引文"}],
  "severity":"minor",
  "suggestion":"指出不足并提出具体修改方向",
  "requires_user_change":false
}
```

id 唯一；句位从 1 开始且不重复；quote 非空且逐字存在于指定正文句；dimension 必须适用；severity=minor/major/blocking。成联改动必须在 issues.locations 先指出这两句的关联问题，不能在修改阶段悄悄扩范围。major 的对应维度须低于 .75，blocking 须低于 .6；任何低于 .6 的维度必须有对应 major/blocking 问题。模型不能一边报告阻断问题一边给高分或宣布 ready。

uncertainties 是 `{"reason":"尚未解决的具体不确定项","blocking":true}` 数组。涉及必须要求时不能降为非阻断。recommend_revision 必须布尔；true 必须有具体问题。minor 可明确决定保留；major/blocking 不能当作 ready。

conclusion 由契约推导并核对：任何问题 requires_user_change=true → needs_clarification；否则阻断 uncertainty 或未满足自由文字用户约束 → unresolved；否则 recommend_revision=true → revise；否则 ready。需要改变要求时保留原稿，不能靠本阶段改写 request 自行授权。未知典故含义应保留 unknown 并给出原因，不虚构来源，也不把出现典故名称当作语义证明。

## 局部修改生成契约

```json
{
  "packages": [
    {
      "id":"m1",
      "binding":"照抄当前 accepted 的完整 binding 对象",
      "revision":8,
      "edits":[{"line_index":1,"old":"当前完整原句","new":"拟替换的完整新句"}],
      "issue_ids":["i1"],
      "reason":"修改如何解决所列问题及维持全诗关系"
    }
  ],
  "needs_user_change":false,
  "reason":"本轮修改策略或保留原稿的理由"
}
```

每包一个修改 ID，在本运行历史中不得复用。binding/revision 必须匹配 accepted；old 必须与当前完整原句完全相同；new 非空且有实际变化。issue_ids 唯一且来自当前审校；只允许其 locations 中的句位。每包 1 句，或 2 句且为同一联的奇数句及下一句。多句包整体作为一个版本；不允许跨联/整诗重写、重复句位、其他 undeclared 字段。模型确需其他位置时，应在下一次全诗审校中先明确问题；当前格式错误不会写入一部分候选。

packages=[] 允许表示没有值得尝试的候选，随后重新审校决定保留或仍未解决；不会擅自清掉问题。needs_user_change=true 时 packages 必须为空，直接 needs_clarification。数组长度受 candidates_per_round 限制。

程序先整批验证格式和归属，再按输入顺序逐包尝试，在独立 PoemState 字典上应用所有 edits，一次增加 revision。继承全部用户要求、模板、韵部、首句入韵和内容安排。exact 完整句原文及句位保持，exact 短语按原 Day 3 规范化规则验证原字；不改变请求以绕过失败。完整规则 failed 或 unknown、exact 遗漏均记录 rejected 原因且不进入比较。后续包仍可尝试；采用一包后其余旧基准候选作废，原始生成输入仍在 transcript 可查。

## 内容复检

candidate_content 与 final_content 直接使用 [Day 3 内容契约](round-content.md) 的 payload，包含原有 ENVELOPE、filter_sha256、content_version、attempt 和 reviews。读取 `job.context.filtered.content_request`；使用其候选 id、line_index、text_sha256 和逐项 requirement ID，给出 satisfied/violated/unknown、reason、interpretation、evidence。

修改候选须在完整新正文上下文逐句复查所有安排，而非仅被修改句。必用典故/adapt 的承担句即使未改字，也可能受其他句的上下文影响，必须重新请求实际 LLM 语义结果。exacts 由原 check_content/filter_candidates 重算；语义复核通过 review_content 导入；最终以每条必需项在承担句位的 satisfied 聚合。可选用典/inspiration 可以 unknown，仍保留记录；必需项 unknown 或 violated 阻断采纳/完成。题目不会传入正文检查。

## 原稿与修改稿比较契约

```json
{
  "candidate_review":"按上述契约评价完整候选诗的审校对象",
  "dimensions": {
    "theme":{"verdict":"same","reason":"全诗主题的对比依据"},
    "naturalness":{"verdict":"better","reason":"全诗语言的对比依据"},
    "imagery":{"verdict":"same","reason":"意象协调的对比依据"},
    "structure":{"verdict":"same","reason":"衔接推进收束的对比依据"},
    "consistency":{"verdict":"same","reason":"情绪时间视角的对比依据"},
    "expression":{"verdict":"better","reason":"表达的对比依据"},
    "parallelism":{"verdict":"not_applicable","reason":"绝句"}
  },
  "resolved_issue_ids":["i1"],
  "decision":"adopt",
  "reason":"说明整体收益、取舍和选择理由"
}
```

candidate_review 必须绑定候选全诗，不能绑定原稿；原稿审校见 job.context.review。七维比较逐项包含 verdict 和具体 reason；verdict=better/same/worse，必须与两稿同维数值的增减一致；绝句对仗为 not_applicable。不能只评价改句是否漂亮，结构、衔接、意象以及一致性都要明确比较。

decision=retain 始终允许，即使分数更高也可说明不改更好。decision=adopt 还必须同时满足程序标准：候选审校 conclusion=ready；所有适用维度无退步；至少解决一个实际关联问题；程序总分提升至少 .05；当前完整格律及内容仍通过。resolved_issue_ids 只能来自当前包 issue_ids，不得重复。高分不能覆盖硬约束、未满足必须内容、结构退步或未解除问题。

比较建议不满足门槛时记录 ADOPTION_STANDARD_NOT_MET 并保留原稿。通过时完整包原子采用；history 同时保存 base（可恢复的前一接受版本）、候选、规则、内容证据、比较工作身份、原始理由和决定。采用本身不作为质量证据。采用后清空当前审校/最终内容证明，再发 review，未完成新审校不得 completed。所有包被拒绝时对保留原稿重新 review；模型可以解释残留 minor 为何值得保留，不能把尚未解除的 major/blocking 隐藏掉。

## 最终输出与完成条件

final payload：`{"title":"独立诗题","notes":["用户需要时展示的简短说明"]}`。题目为非空、最多 80 字、单行字符串，notes 可为空。说明只能陈述已有规则检测及语义依据支持的内容；真实韵库不确定项不能描述为通过。final 身份封套仍绑定当前正文、版本、规则上下文和审校工作。

处理 final 时程序重新检查：完整 reviewing 诗作；当前全诗规则通过；当前正文内容 artifacts 经 Day 3 重建核验且每个必须项 satisfied；当前运行、分支、正文和版本的全诗审校 conclusion=ready；没有未解决阻断或必需 unknown。随后才调用 PoemState.set_assessment 和 set_status(completed)。缺失审校、旧审校、缺内容或规则失败均不能完成。

`result()` 默认返回 `{title, lines, status}`；`include_notes=True` 增加 notes。题目不是正文第五句/第九句，不参与韵脚，也不能满足正文必含片段。内部 output 还保存 output_version、binding、revision、最终工作 identity；默认不展示分数和历史。未完成时返回 `{title:null, lines:最后接受正文, status, reason}`，不得包装成已完成作品。

## 预算、幂等与恢复

| 配置 | 默认 | 计数 |
| --- | --- | --- |
| max_rounds | 3 | 进入一次 generate 即消耗一轮，允许 0 禁止润色 |
| candidates_per_round | 3 | 每轮提交包数上限，至少 1 |
| max_interactions | 40 | 每次新模型工作和格式修复导出各消耗 1，至少 1 |
| max_format_repairs | 2 | 每工作最多额外修正次数，范围 0–2 |

初次全诗审校和最终标题同样计入交互。候选被拒绝后的重新审校、采用后的新审校也计费。读取/重复提交不扣费。外层身份错误直接抛异常、状态不变；正确寻址而 payload 错误时，舍弃事务副本，保存错误与原响应，并发行有限修复。原始模型输出建议直接作为 payload 字符串传入，严格 JSON 解析（重复键、NaN、语法错误）发生在已计费工作内，不在控制器外无限修 JSON。

成功响应再次导入返回 already_processed；修改成功结果重放报 CHANGED_REPLAY。完全相同已拒绝响应返回 already_rejected；新的修复响应照抄新 attempt。所有修改在独立控制器副本上执行，验证错误不留下半包 accepted、history 或 candidate 更新。

status 为 waiting_for_llm / completed / needs_clarification / incomplete / budget_exhausted；内部 running 只在事务中出现。预算耗尽、格式修复耗尽、必须项失败/unknown 时保留最后接受版本、已有候选检查与历史，给出 reason，不扩预算、不标 completed。pending 只在 waiting_for_llm 非空。因缺模型结果暂停无需做终止操作，保存并稍后恢复即可。

完整快照为 `{polish, sha256}`，包含原 Search 快照、run_id、配置、已用预算、accepted、当前审校/内容、候选队列、pending、history、reviews、原响应 transcript、幂等记录和最终 output。恢复时从交接重新构造并重放已保存响应，逐项比较重建运行；不只相信摘要或 completed 标签。重放不调用模型、不新增实际预算，结果须与原状态完全相同。当前规则资源与原证明不一致时拒绝恢复（SNAPSHOT_REPLAY_MISMATCH 或交接错误），不能继续沿用旧通过标记。该回放成本随有界工作记录增长，保存文件也包含完整审计信息。

save/load 和 CLI 复用 Day 3 的[独立工作区](runtime-workspace.md)解析及原子新文件写入，禁止覆盖、禁止向 Skill 目录写入。所有内置模板和韵库相对脚本定位，无开发机绝对路径。恢复前一接受版本可从对应历史快照及 transcript 找到采用前检查点；不能把单独 base 字典当作已通过 Day 5 的新运行。

## CLI

```sh
python scripts/poetry_polish.py start --input examples/search.json --path-id p4 --output examples/polish-01.json
python scripts/poetry_polish.py next --input examples/polish-01.json --output examples/polish-job-01.json
# 运行 Skill 的 LLM 根据 job.stage 和本契约写入 response（身份字段 + payload）
python scripts/poetry_polish.py submit --input examples/polish-01.json --response examples/polish-response-01.json --output examples/polish-02.json
python scripts/poetry_polish.py result --input examples/polish-final.json --output examples/poem.json --include-notes
```

示例 p4 仅表示参数形式，必须替换为输入快照中实际选定的 path_id。start 支持 --max-rounds、--candidates-per-round、--max-interactions、--max-format-repairs。next 仅导出工作，不用于恢复；恢复始终读取完整 Polish 快照。engine 注入只用于可控测试，不接收模型自报规则引擎或声韵结论。

## 确定性示例与验收

```sh
python scripts/day5_example.py --output examples/day5-new.json
python scripts/day5_example.py --adopt --output examples/day5-adopt-new.json
python -m unittest discover -s scripts -p 'test_*.py'
```

默认示例：Day 4 完整草稿 → LLM 夹具报告 minor → 提议字数错误的修改 → 程序拒绝并保留原稿 → 重新审校认为保留更好 → 最终完成。`--adopt` 则用通过测试韵库的修改，完成全诗比较、原子采用、新正文审校和最终验收。保存的默认示例为 `examples/day5-result.json`；`day5_example.py` 明确使用微型韵库和固定模型响应，其正文重复字串不是文学作品。

测试覆盖无修改完成、单句和成联采用、格律失败/unknown 拒绝、exact 完整句与短语及位置保护、修改别句后 allusion/adapt 失效、全诗结构退步保留、低收益不采用、工作/分支/版本归属、旧审校失效、无效包无部分写入、评分/引文/原文契约、预算和格式修复、幂等、保存恢复与篡改拒绝、最终证明缺失、标题隔离，以及项目整体迁移后从其他工作目录运行。原有 Day 1–4 测试保持不变。

2026-09-15 验证：实现前原有 129 项测试通过；新增 37 项 Day 5 测试后，全量 166 项通过。迁移测试另使用项目自带真实韵库跑通 CLI start/next/submit/result，固定文学响应仍是夹具。Skill 结构校验通过，保存示例经 Polish.load 完整重放验证：completed，1 轮润色、4 次交互，修改因 RULES_FAILED_OR_UNKNOWN 被拒绝，保留原稿。

这些测试证明控制流程和结果绑定按契约运行，不能证明真实模型文学质量、语义报告准确率或评分一致性。真实创作质量留待后续评测；平水韵资源及规则覆盖仍受既有引擎边界限制。
