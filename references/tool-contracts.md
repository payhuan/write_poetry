# 底层工具契约

当前可运行入口包括输入解析、状态初始化、模板绑定、纯规则格律检测、[Day 3 单轮候选流程](poetry-round.md)、[Day 4 搜索控制](poetry-search.md) 和 [Day 5 全诗审校与润色](poetry-polish.md)。

- `check_candidate(candidate, state, *, readings=None, duplicate_review=None) -> ConstraintResult`
- `check_poem(state, *, readings=None, duplicate_review=None) -> ConstraintResult`

实现位于 scripts/constraint_engine.py。候选包含 id、text、从 1 开始的 line_index；按该句位检查，重复统计替换原句。state 为 PoemState 或合法状态字典，readings 按“句号:字位”定位；duplicate_review 必须来自绑定请求的结构化 AI 审查。调用错误抛 ValueError；格律失败和资料不足通过结果表达；检测不修改状态。

以下为旧版可读取的结果示例；新结果必须附带下述对象绑定字段：

```json
{"revision":3,"valid":null,"checks":{"length":{"status":"pass","required":true},"pingze":{"status":"unknown","required":true},"rhyme":{"status":"not_applicable","required":true},"duplicate":{"status":"pass","required":false,"occurrences":[]}},"errors":[],"warnings":[]}
```

单项状态为 pass、fail、unknown、not_run、not_applicable。任一必需项失败为 valid=false，必需项未知或未执行为 null，其余为 true。required 省略默认 true；只能为布尔值；正常模式下 length、pingze、rhyme、reading、duplicate 均为必需项。重字未经语义审查为 unknown；仅叠字、意群并列或意群递进通过，错误复现失败。候选非韵脚句的押韵不适用不阻断。

新结果增加 binding_version=1、scope（candidate/poem）、subject 和 context_sha256。候选 subject 包含 candidate_id、line_index、text_sha256；全诗 subject 包含完整原始 lines 数组的 text_sha256。上下文摘要覆盖正文、完整模板快照、韵律、约束与创作上下文，具体字段和编码见 [检测结果绑定](constraint-engine.md)。

PoemState.set_assessment 只接收匹配当前全诗的结果；evaluate_candidate 只接收匹配该候选及当前上下文的结果。revision 也必须一致。新晋级 retained 要求 valid=true；新晋级 completed 要求当前全诗绑定通过。旧结果仍可读取保存并保留历史标签，但不能用于新写入或晋级，必须重检。旧结果没有 required 时保持原必需项语义。

规则范围、数据限制、逐位置读音选择、重字语义审查格式和命令行见 [纯规则格律检测](constraint-engine.md)。引擎不调用模型；文学评价及受约束的重字语义判断由 agent 提供，候选校验、评分计算和稳定排序由 poetry_round.py 完成，不实现语义对仗程序判定或搜索控制。

单轮公开接口：`export_context(state, line_index)`、`filter_candidates(state, context, payload, engine=None)`、`validate_scores(filtered, payload)`、`finish_round(state, filtered, scores=None, engine=None)`。engine 为仅关键字参数，允许复用同一个 ConstraintEngine 及其韵库。前三个接口不写状态，finish_round 使用 PoemState.record_candidate_batch 原子写入候选历史，返回完整审计报告；不写全诗检查，不追加正文。具体 JSON、错误码、评分尺度及 CLI 见单轮文档。

内容扩展接口：`export_content_plan(state)`、`export_context(state, line_index, content_plan=...)`、`filter_candidates(state, context, payload=None, engine=...)`（payload 仅锁定原句时可省略）、`review_content(state, filtered, payload, engine=...)`、`read_content_fulfillment(state, history_candidate_id)`。content_plan/engine 为仅关键字参数。模型安排、语义解释和内容结论分别保存，不改 request 或纯规则 checks；内容评价导入后使用新 score_request 评分。完整契约见 [内容安排与履行](round-content.md)。

搜索接口位于 scripts/poetry_search.py：`Search.create(state, content_plan=..., engine=...)`、`next_job()`、`submit(response)`、`to_dict/from_dict`、`save/load`。控制器调用上述 Day 3 接口完成原子候选采用；整体保存预算、路径、备选检查点和待执行工作。规则与内容通过只交接 reviewing 草稿，详见搜索契约。

Day 5 接口位于 scripts/poetry_polish.py：`Polish.create(search_snapshot, path_id, *, engine=None, max_rounds=3, candidates_per_round=3, max_interactions=40, max_format_repairs=2)`、`next_job()`、`submit(response)`、`result(include_notes=False)`、`to_dict/from_dict`、`save/load`。仅接受完整 Search 快照和明确的草稿路径，所有模型结果绑定工作、正文、上下文和版本。完整诗通过当前规则、内容、全诗审校及四项最终语义红线后才设置 completed；题目独立保存。模型契约和采用标准见 Day 5 文档。


