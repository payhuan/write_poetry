# Day 3 内容安排与履行

Day 4 集成见 [搜索控制](poetry-search.md)：搜索固定已确认的 assignments，在分支追加后重新绑定当前计划；完整正文重新调用本模块进行内容验收，不复用旧候选的 satisfied。本页以下说明仍是单轮接口的职责边界。

本模块消费已经解析的 request.allusions 和 request.required_fragments，不重新做输入解析。读取缺失字段视为空数组，不修改或补写旧状态。原始要求、source_excerpt 和 description 仍属于用户；模型的句位安排保存为独立 content_plan，语义解释保存为内容评价。二者都不能回写 request。

## 单轮顺序与接口

1. `export_content_plan(state)` / CLI `plan` 导出与当前 revision、上下文绑定的安排骨架。用户已锁位置照抄，未安排项为 null。
2. agent 安排未定位的 exact/adapt 片段和 required=true 典故的承担句位，填入独立 reason；inspiration 必须保持 null，可选典故可为 null。通过 `export_context(state, line_index, content_plan=plan)` 校验并导出上下文。
3. 未安排必需项时返回 planning_required / CONTENT_ASSIGNMENT_REQUIRED，不进入候选处理。本句有 `locked_candidate` 时直接 `filter_candidates(state, context, engine=engine)`，跳过 LLM 生成；其他情况沿用候选 JSON。
4. `filter_candidates` 复用 Constraint Engine 检测原文，同时独立计算 exact 内容。结果保留纯规则 category/checks，另有 content_result。只有格律通过、exact 内容未明确失败的候选进入 `content_request` 的语义评价。
5. agent 按 content_request 对本句用典、adapt 和 inspiration 逐项评价；用典项同时读取 `allusion_reference`，规则见 [典故查询与规范化](allusion-lookup.md)。`review_content(state, filtered, payload, engine=engine)` 导入并校验；歧义典故不能判 satisfied。使用返回结果的最新 score_request 进行文学评分。
6. `finish_round` 复核整批规则和内容评价归属，校验评分后原子记录。未履行的当前必需内容不接受文学评分，也不能获得 retained 或推荐。无语义请求时可直接 finish；可选内容评价缺失不会阻断，仍诚实记录 unknown。

`filtered.context` 是该批次唯一的权威完整上下文；派生的 `content_request` 通过封套摘要绑定它，不重复携带上下文。模型处理内容请求时应结合这一同级字段读取创作依据。

所有接口都可从 poetry_round 导入；实现分为 scripts/round_content.py 和现有 scripts/poetry_round.py。除 finish 外不写 PoemState。可以在同一流程传入同一个 ConstraintEngine，复用韵库。新语义阶段也遵守一次输出加两次格式修正的上限（attempt=1、2、3），不自动调用模型或搜索。

可从任意目录运行；以下相对路径位于[独立工作区](runtime-workspace.md)，无需在 Skill 包内创建运行目录：

```sh
python scripts/poetry_round.py plan --state examples/content-run/state.json --output examples/content-run/plan.json
# agent 阅读 request 与 plan 骨架，只修改安排，保存为 arranged.json
python scripts/poetry_round.py export --state examples/content-run/state.json --line 2 --content-plan examples/content-run/arranged.json --output examples/content-run/context.json
# locked_candidate 非 null 时无需 --candidates；否则提供既有候选 JSON
python scripts/poetry_round.py filter --state examples/content-run/state.json --context examples/content-run/context.json --output examples/content-run/filtered.json
# agent 根据 content_request 输出 reviews.json
python scripts/poetry_round.py review-content --state examples/content-run/state.json --filtered examples/content-run/filtered.json --reviews examples/content-run/reviews.json --output examples/content-run/reviewed.json
# agent 根据 reviewed.json 中最新 score_request 输出 scores.json
python scripts/poetry_round.py finish --state examples/content-run/state.json --filtered examples/content-run/reviewed.json --scores examples/content-run/scores.json --output examples/content-run/result.json
```

锁定原文与格律明确冲突时，结果为 requires_user_adjustment / LOCKED_EXACT_RULE_CONFLICT，保留引擎逐项原因，请用户调整要求，不擅改诗体、韵部、原句或读音。规则未知仍为 unresolved，不能当作通过。exact 短语缺失属于内容失败，不会伪造格律失败。

## 安排 JSON

导出骨架保留真实 content_version、revision 和 context_sha256。agent 只安排 line_index 并填写独立 reason；下例假定 f1 未指定位置，a1 为必用典故：

```json
{
  "content_version": "round-content-2",
  "revision": 2,
  "context_sha256": "<照抄 plan 导出值>",
  "assignments": [
    {"kind": "fragment", "requirement_id": "f1", "line_index": 2, "reason": "模型安排：第二句承接山寺秋景"},
    {"kind": "allusion", "requirement_id": "a1", "line_index": 3, "reason": "模型安排：第三句借梦蝶转入物我感受"}
  ]
}
```

每项要求都有一条记录，kind+requirement_id 联合关联，允许两个输入数组恰好使用相同 ID。缺项、重复项、额外字段、错误句位或改动用户已指定句位均拒绝。新安排产生的固定句/短语位置冲突复用既有 check_fragment_structure 校验，不做全诗布局搜索或格律可行性推断。模型安排引用独立 reason，不能将其放入 source_excerpt 冒充用户依据。

计划绑定当前正文和需求，因此采用诗句、替换正文或修改规划后，旧计划不能直接导入。应重新导出骨架，由 agent 复核并沿用仍适合的安排，再导入当前批次；不清空历史，也不暗中转移原有履行任务。

## 原样匹配与履行记录

本句锁定 exact 完整句时，程序从 request 直接构造唯一候选，ID 为 `locked:片段ID`，保留原文所有标点和空白。模型不能提供改写候选；传入与程序锁定封套不同的候选报 LOCKED_EXACT_LINE。多个归一后相同的固定句要求可共用一个候选，仍分别记录各自 requirement ID。

exact 匹配复用 text_normalizer.normalize_line 的既有空白、标点和句段口径。完整句要求归一后整句相等，phrase 要求归一后连续子串相等；多句拼接不匹配。不做同义替换、简繁转换、字序调整。原始候选和原始片段始终保存，匹配文本仅用于确定性判断。

每个候选的 content_result 包含当前所有要求，以及候选 scope/subject、revision、context_sha256、batch_id、generation_sha256、plan_sha256。每项记录 kind、requirement_id、承担句位、required、blocking、status、method、reason、evidence、interpretation、arrangement 和 arrangement_source：

| status | 含义 |
| --- | --- |
| pending | 安排在后续、尚无正文的句位，不阻断当前候选 |
| satisfied | exact 已匹配，或 agent 按对应语义标准明确报告满足 |
| violated | 相应文本未符合 exact，或 agent 报告未体现应保留的语义 |
| unknown | 缺少文本、语义尚未判断、典故含义不明确等 |

`content_result.valid` **只表示当前候选的内容准入**，不是全诗内容验收。当前应履行的必需项 violated → false、unknown → null；其余 true。required=false 典故和 inspiration 不阻断。安排在其他句位的项不会导致当前候选失败：已有正文的 exact 状态会重新计算；先前句位的语义项保留 unknown 待复核，不复用历史候选的 satisfied。所有这些记录都保留给后续全诗审校。

纯规则 category 和 counts.passed 保持原义；有内容计划时结果另列 content_eligible、content_violated、content_unknown 计数以及 content_blocked_ids。若格律通过但无候选满足当前必需内容，返回 CONTENT_REQUIREMENTS_VIOLATED 或 CONTENT_REQUIREMENTS_UNKNOWN，推荐为 null。文学平均分不能抵消内容失败或必需项未知。

## LLM 语义评价契约

只评价程序 content_request.candidates 指定的候选与要求。必用典故必须解释**具体表达如何体现典故的含义**；仅出现典故名称不算程序已确认。`resolved` 用典以同项引用中的规范主典、简释与出处原文为依据；`ambiguous` 必须先消歧，程序拒绝 satisfied；`not_found` 是局部语料未命中，须用可靠外部来源核实，否则为 unknown。每条用典评价还必须按 [用典规范](allusion-usage.md) 提交 `allusion_analysis`；两道门任一项失败或未知、没有原典到诗境的具体映射、存在 E01-E12，均不得判 satisfied。adapt 判断主要意思是否保留，不要求逐字相同。inspiration 判断启发关系，不要求原文出现；即使关系不明显也不阻断。不编造解释或出处。

```json
{
  "contract_version": "poetry-round-2",
  "batch_id": "<照抄值>",
  "context_sha256": "<照抄值>",
  "generation_sha256": "<照抄值>",
  "filter_sha256": "<照抄 content_request 值>",
  "content_version": "round-content-2",
  "attempt": 1,
  "reviews": [
    {
      "id": "c1",
      "text_sha256": "<照抄该候选摘要>",
      "line_index": 3,
      "requirements": [
        {
          "kind": "allusion", "requirement_id": "a1", "status": "unknown",
          "reason": "尚不能确定此处表达是否涉及梦蝶的物我关系，需要进一步确认含义。",
          "interpretation": "", "evidence": [],
          "allusion_analysis": {
            "source_basis": {"kind": "unverified", "references": []},
            "classification": {"source_type": "unknown", "visibility": "unknown", "semantic_relation": "unknown", "transformation": "unknown"},
            "fact_checks": {
              "persons": {"status": "unknown", "reason": "待核实"},
              "relationships": {"status": "unknown", "reason": "待核实"},
              "event": {"status": "unknown", "reason": "待核实"},
              "source": {"status": "unknown", "reason": "待核实"},
              "core_meaning": {"status": "unknown", "reason": "待核实"}
            },
            "mapping": [],
            "fit_checks": {
              "semantic": {"status": "unknown", "reason": "待判断"},
              "situation": {"status": "unknown", "reason": "待判断"},
              "emotion": {"status": "unknown", "reason": "待判断"},
              "identity": {"status": "unknown", "reason": "待判断"},
              "function": {"status": "unknown", "reason": "待判断"}
            },
            "function": "尚不能确认表达功能",
            "reversal_signals": [], "error_codes": [],
            "quality": {
              "dimensions": {"semantic_fit": 0, "context_fit": 0, "coherence": 0, "naturalness": 0, "semantic_gain": 0, "transformation": 0},
              "penalties": {"obtrusiveness": 0, "redundancy": 0}, "total": 0
            }
          }
        }
      ]
    }
  ]
}
```

顶层和每项字段严格如上。每个被请求的候选和本句语义要求必须且只能出现一次。status 只能为 satisfied/violated/unknown，pending 由程序按安排确定，模型不能借 pending 跳过本句必需项。reason 非空，reason/interpretation 各最多 1000 字符；satisfied 必须同时有非空 interpretation 和至少一条 evidence，例如 `{"line_index":3,"quote":"候选中的原字表达"}`。quote 必须逐字来自此候选、句位相符。unknown/violated 可以无证据，但必须解释原因。

程序只验证格式、候选 ID、文本摘要、句位、批次、版本、上下文、要求覆盖和引文确实存在，**不宣称能证明语义报告正确**。用户典故名称的出现不会自动赋 satisfied。程序不会联网编造典故来源；需要资料核实或澄清的语义由 agent 另行处理，未确定前保留 unknown。

review-content 会复核原始过滤结果，再导入语义报告，生成新的 filter_sha256 和 score_request。文学评分必须绑定这个新摘要，不能复用导入内容评价之前的分数。要修正内容评价，请对原始 filtered.json 重新 review-content，随后重新评分；不要改写已封装的履行结果。

## 保存、失效与后续全诗审校

finish 仍使用现有 PoemState.record_candidate_batch 原子写入；没有新状态 schema，也没有重建解析入口。candidate_history.reason 的 JSON 字符串增加完整 content_result。结果包 report 保留 content_plan，filter_artifact 保留用户要求、安排、原始内容评价、确定性结果和语义解释。所有结果都属于候选，不代表已采用；正文仍须显式采用。

通过 `read_content_fulfillment(state, history_candidate_id)` 读取可复用的当前履行结果。正文、规划或要求变化后，revision 或上下文不匹配即报 STALE_CONTENT_RESULT；历史 reason 中的旧 satisfied 只是历史，不再是当前结论。候选正式采用也会改变正文上下文，不能把采用前的候选履行直接当作全诗已满足证明。

本阶段仍不做全诗搜索或最终内容验收。后续审校须针对最终采用正文逐项核对：所有必含片段的位置与内容、全部 required=true 典故的实际表达、adapt 的主要意思、重复或互相矛盾的语义安排，以及未履行、unknown、早先已被替换的承担句。当前结果中的 pending 或非当前句位的 violated 不会阻断本轮推荐，但绝不能据此宣称全诗验收通过。

## 验证结果

2026-09-15：完整 unittest 共 102 项通过（接入前 81 项、新增 21 项），Skill 结构校验通过。新增 scripts/test_round_content.py 用固定模型输出验证原句直检、短语缺失、adapt/inspiration、必用和可选典故、后续 pending、语义归属与证据、平均分不能掩盖遗漏、履行状态失效和旧状态兼容。迁移测试复制整个项目，从其他工作目录运行 plan/export/filter/review-content/finish，加载项目内真实韵库并恢复内容履行记录；不依赖随机生成诗句。
