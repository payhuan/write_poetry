# 完整句／联生成（poetry-unit-1 与 literary-2）

普通单位仍生成完整单句。五律、七律的第 3–4 句和第 5–6 句由 `scripts/poetry_unit.py` 一次生成完整联，
两句逐一通过格律、押韵、重字、内容、读音与用典检查后，才以 `PoemState.append_unit` 一次提交。任一句失败或
未知都不留下半联，整联只增加一次 revision。

每个新候选必须说明 `expression_path`、`semantic_increment`、`risks`，并提交 `semantic_lock`：保护核心语义、
事实、意象、关系、情感方向、结构功能和已确认典故。锁必须覆盖 PoemPlan 的保护语义。规划无法自然实现时，
生成结果可返回 `replan_required`，说明冲突和最小重规划范围；Search 以 `REPLAN_REQUIRED` 停止，不暗改诗意。

`literary-2` 评价任务完成、语义推进、物象关系、上下文一致、含蓄度、自然度、新鲜度、后文空间和对仗。
颔联、颈联必须另外提交语义关系、句法骨架、词性／成分对应、意象关系及合掌检查，每项带状态和理由。
对仗分进入 Beam 排序；低分、fail 或 unknown 不会被 Python 改写成格律失败。程序只校验评分契约、归属和数值，
不声称能判断真实文学质量。
