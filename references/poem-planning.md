# 全诗语义规划（poem-plan-2）

`scripts/poem_planning.py` 定义独立、版本化并绑定 PoemState 的 `PoemPlan`。新建 Search 必须传入由
`make_plan_artifact(state, plan)` 生成的完整工件；`load_plan_artifact` 会同时验证摘要、状态绑定和规划内容。
规划只写任务、关系和保护边界，不写完整诗句。

规划包含：核心命题、主体、处境、触发事件、主情绪、内在张力、结尾状态和语域；时间、空间、视角以及
实景／回忆／想象关系；情绪路径与核心意象的发展；内容与用典安排；风险、格律边界和自检。所有事实均以
`provenance.source=user|inferred` 区分用户事实与规划推断，推断不能冒充用户原话。

绝句按四句规划，不套用起承转合标签。律诗按首联、颔联、颈联、尾联规划，并依次标记起、承、转、合，说明各联如何服务核心命题。每个单位须给出主要任务、可表达内容、语义增量、与前后单位的关系、保护语义和暂不可提前表达的内容。颔联、颈联还须规划对仗关系、语义轴、句法策略及避免合掌要求，不得预填诗句。

律诗颈联必须提供 `turn_plan`：转向轴（行动／状态、情绪／立场、认知、意义、时空、意象关系或其他）、转前状态、变化、转后状态、前文铺垫、与前后联的关系、对本联 `primary_task` 与 `semantic_increment` 的作用、对保护语义的处理，以及变化产生的新意义或张力。转向须与职责、增量、前后关系和保护语义相容。单纯增加下一件事不够；但转向不要求换场景或意象。绝句不要求这些标签或额外自检项。

如误填转向轴（例如 `emotion`），仍返回 `TURN_PLAN_REQUIRED`，但 `details` 会列出 `location`（`structure_plan[2].turn_plan.axis`，索引从 0 开始）、`received` 和 `allowed_values`。情绪／立场轴应填 `emotion_stance`。Search CLI 将该详情保留在标准错误输出的 JSON 中。

律诗自检还须通过 `arc_roles_assigned`、`neck_couplet_changes_direction`、`turn_is_more_than_next_event`、`ending_resolves_or_reframes`。验证器校验字段、位置和状态链，不替模型判断文学上的转向是否成立。若转向会损害用户事实、典故或保护语义，应保留代价说明并重新规划，不为满足标签强行跳景。缺单位、重复单位、无依据跳景、对仗规划缺失、转前后状态未变化、典故安排指向未知单位或自检未通过都会被拒绝。

`planning_basis(state)` 是稳定绑定；正文、请求、模板、韵部或规划上下文改变后，旧规划不能移植。
`export_plan_request(state)` 可作为模型规划提示的结构化输入。旧 `poetry-search-1` 至 `poetry-search-4` 快照只可审计读取；继续创作
须从其原始 PoemState 重新规划并创建当前版本的 Search 运行。
