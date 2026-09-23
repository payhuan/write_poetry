# 完整句／联生成（poetry-unit-2 与 literary-5）

普通单位仍生成完整单句。五律、七律的第 3–4 句和第 5–6 句由 `scripts/poetry_unit.py` 一次生成完整联，两句逐一通过格律、押韵、重字、内容、读音与用典检查后，才以 `PoemState.append_unit` 一次提交。任一句失败或未知都不留下半联，整联只增加一次 revision。

每个新候选必须说明 `expression_path`、`semantic_increment`、`risks`，并提交 `semantic_lock`：保护核心语义、事实、意象、关系、情感方向、结构功能和已确认典故。锁必须覆盖 PoemPlan 的保护语义。规划无法自然实现时，生成结果可返回 `replan_required`，说明冲突和最小重规划范围；Search 以 `REPLAN_REQUIRED` 停止，不暗改诗意。

`literary-5` 评价任务完成、语义推进、物象关系、上下文一致、含蓄度、自然度、新鲜度、后文空间和对仗。对仗权重提高到 `.25`。律诗颔联按“承”接住并展开前联职责；颈联按规划的“转”改变前文状态或解释，说明铺垫并给尾联留下收束空间。单纯增加下一件事不算转向；转向可发生在行动、情绪、认知或意义上，不要求换场景或意象。若无法在保护事实、典故和语义的前提下完成，返回 `replan_required`。

对仗必须双层评价：

- `character_alignment` 逐字位提交完整上下句对应字符、句法功能、状态和理由，覆盖五言／七言全联。
- `structure_alignment` 把上下句分别切分成有序、连续、不重叠且完整覆盖整句的词组／句法成分；逐段提交位置、原文、句法功能、对应关系、状态与理由。上下句分段可以长短不同。

两个层次分别给 `character_score` 与 `structure_score`，取值均为 0–1；`parallelism.score` 必须是两者的算术平均。对应状态为 `pass`、`weak`、`fail` 或 `unknown`。任一层出现 `weak` 时该层分数上限为 `.74`，`unknown` 上限为 `.59`，`fail` 上限为 `.49`。上限与较高权重一起拉开候选排序，但仍是软约束，不会只因对仗低分直接淘汰候选；状态和分数相互矛盾则拒绝该评分响应。逐字位不要求字面同义，也不能代替词组／句法层次的对应判断。

整联内容审查时，`job.context` 是完整依据；嵌套的 `job.filtered.content_request.requests[*].request` 省略重复上下文，只保留该句候选、要求和绑定摘要。
