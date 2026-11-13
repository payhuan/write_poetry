---
name: write-poetry
description: 根据中文文字描述创作五言绝句、七言绝句、五言律诗或七言律诗，适用于主题、场景或指定情绪的近体诗创作。
---

# 赋诗

先规划全诗，再生成完整句或联，随后炼字。运行本 Skill 的 agent 负责语义理解、候选生成和文学评价，Python 负责确定性校验、规则过滤与状态记录，无需另配模型 API。

## 当前实现边界

目前提供输入提取与校验、PoemState 保存恢复、16 个模板的选择与绑定、结构化 [全诗语义规划](references/poem-planning.md)、Day 3 [完整句／联生成](references/unit-generation.md)、Day 4 全局 Top-K / Beam Search / 有限回溯，以及 Day 5 全诗审校、[逐单位炼字](references/word-refinement.md)和最终验收。状态 API 见 [输入与状态](references/state.md)，运行文件位置见 [独立工作区](references/runtime-workspace.md)。规则边界见 [检测引擎](references/constraint-engine.md)，单轮契约见 [候选与文学评分](references/poetry-round.md)，多路径接口见 [搜索控制](references/poetry-search.md)，最终阶段见 [全诗审校与润色](references/poetry-polish.md)。文学评价与语义判断由运行 Skill 的 LLM 提供；程序校验契约、证据归属和硬约束，不把 unknown 或未执行的检测报告为通过。

## 工作流

Search 或 Polish 返回格式错误时，保存当前快照，读取 `next_compact_job()` 中的 `repair_feedback`，根据错误生成新响应后再提交。收到 `already_rejected` 时当前尝试仍可用；只换 attempt 的响应无需再次导入。全诗审校的律诗对仗汇总分填 `null`，提交颔联、颈联各自的逐字和逐结构评分，由程序汇总。候选的 `semantic_lock.protected_meaning` 应包含规划中每一项保护语义的原文，`structural_function` 使用该单位的 `primary_task`。

遇到用典、必含诗句或用户灵感时，先按 [内容要求](references/content-requirements.md) 提取 allusions 和 required_fragments，区分原样保留、允许润色及仅作灵感。用典名称按 [典故查询与规范化](references/allusion-lookup.md) 查询：唯一精确入口才能自动关联规范主典，歧义先消解，模糊结果不能自动改名，本地未命中不能解释为典故不存在；用典是否成立按 [用典规范](references/allusion-usage.md) 经过事实门和诗境映射门。解析仅作结构和字数冲突检查，不宣称内容或格律已满足。Day 3 接入按 [内容安排与履行](references/round-content.md)：模型安排、典故引用和解释与 request 分开保存，不能回写用户原文依据。

1. 按 [输入解析](references/input-parsing.md) 理解自然语言并输出提取 JSON，分别记录诗体与 meter 中的起式、首句是否入韵、指定模板。保留原文依据、否定限制和待澄清问题；未指定与明确不入韵必须区分。
2. 使用 `scripts/parse_input.py --text-file request.txt --extraction extraction.json --output state.json` 校验并初始化 PoemState。使用 Python 3，脚本路径根据本 Skill 目录解析。需要澄清时先解决冲突，不进入生成流程。
3. 复核 [输入与状态](references/state.md) 中的原文、缺省决策和未提供依据的字段。未指定诗体时优先从指定模板确定，否则默认七言律诗。唯一匹配已自动绑定；部分诗格要求保留供规划补选。已有规范化 JSON 也可使用 `scripts/init_state.py --input request.json --output state.json`。
4. 按 [创作指导](references/composition-guide.md) 和 [全诗语义规划](references/poem-planning.md) 先建立绑定状态的 `poem-plan-2`。规划核心命题、时空视角、情绪与意象发展以及每句／联的任务、增量、前后关系、保护语义和暂不可提前表达的内容，不预写诗句。律诗首联、颔联、颈联、尾联分别承担起承转合并说明各自如何服务命题；颈联规划具体状态／立场／认知／意义变化、前文铺垫和尾联收束作用。转向不要求换景，单纯顺叙下一事件不足；不得为造转破坏事实、典故或保护语义。颔联、颈联还规划对仗关系、语义轴、句法策略和避免合掌要求。绝句不新增起承转合门槛。区分用户事实与模型推断；缺单位、跳景无依据、语义重复或内容安排错位先修正规划。需要选典时先整理说话者、场景、事件、时间关系、情绪方向和待表达语义单元，再检索，不能由单个主题词触发典故。用户未要求而由模型建议的典故须与无典表达比较，无语义增益便不用。未绑定时按 [诗体说明](references/poem-types.md) 在兼容用户要求的模板中选择并调用 `poem_templates.py bind`，简短说明所选格式，再选择韵部。
5. 需要候选时读 [单轮流程](references/poetry-round.md) 与 [完整句／联生成](references/unit-generation.md)。普通位置生成完整单句；律诗第 3–4、5–6 句一次生成完整联。每个候选提交表达路径、语义增量、风险和 `semantic_lock`；规划不可自然实现时返回 `REPLAN_REQUIRED`，不能暗改诗意。整联两句全部通过才由 `append_unit` 一次提交。韵脚句尚未选择韵部时先完成规划。有内容要求时先安排承担句位；inspiration 不锁句位。本句有 locked_candidate 时跳过模型生成，直接检测原文。`reading.status=unknown` 与 `duplicate.status=unknown` 必须先分别发起范围绑定复核并由规则引擎重检，之后才可请求内容语义或评分；联级证据可引用联中任一句，但逐句的 candidate-scope 证明不能互相替代。
6. 有 `content_request.candidates` 时，agent 按内容契约逐项说明用典、adapt 或 inspiration 的实际表达，使用 `review-content` 导入。用典评价读取同项 `allusion_reference`：resolved 按主典和出处解释，ambiguous 不得判 satisfied，not_found 需另有可靠依据，否则留 unknown；并提交 `allusion_analysis`，逐项验证人物、关系、事件、出处、核心典义及原典到当前诗境的映射。反用须有诗句中的明确反转信号，错误代码或任一门未知/失败均不能 satisfied，总分不能覆盖硬错误。必用典故和 adapt 的当前履行必须确认，不能以平均文学分代替。随后只对最新 `score_request.candidates` 评分；`finish` 验证、排序并原子记录候选和完整内容履行依据。每个模型阶段（生成、内容评价、文学评分）首次输出后最多修正格式两次，attempt 依次为 1、2、3；仍有错误即停止并报告。所有中间文件和最终状态包保存在独立运行工作区，不写入 Skill 安装目录。
7. 单轮仍只推荐。需要推进全诗时读 [搜索控制](references/poetry-search.md)，以 PoemState 和必需的 poem-plan 创建 Search，反复读取 next_job。普通句按 generate → reading/duplicate（如有）→ content → score 处理；中间联相应执行 unit_reading/unit_duplicate，所有检查通过后才做内容复核和评分。全诗出口及 Day 5 改稿均对当前正文重新检查；正文发生变化时旧重字证明失效，必须按新范围复核。初稿播种时先录入全部句子，再基于完整正文重新规划 PoemPlan 与内容安排，不能只改旧绑定；可选运行 `constraint_engine.py --state` 预检。Day 5 按计划逐联审校律诗起承转合，绝句不强加四段标签；颈联只看是否发生有铺垫的方向变化，不要求换景。按 `literary-6` 对颔联、颈联同时做逐字与逐结构对仗评价，分别给分后取平均；较高权重和状态分数上限会明显影响排序，但对仗仍是软约束，不直接淘汰诗稿。旧 `poetry-search-1` 至 `poetry-search-8` 快照只读；继续创作须重新规划并新建运行。
8. ready_for_review 是 Day 4 交接状态，PoemState 仍为 reviewing。继续创作时读 [全诗审校与润色](references/poetry-polish.md)，从完整 Search 快照显式选择一个 path_id 创建 Polish，不默认取首稿，不把任意状态字典当已验收草稿。程序重新检测规则并核验当前内容证据；交接失败先报告原因，不能手动改状态或复用历史候选评分。
9. 按 next_job.stage 提供全诗 review、问题修复 generate、refinement、reflow、candidate_content、compare/refinement_compare、final_content、redline 或 final JSON。全诗 review ready 后仍须按 [逐单位炼字](references/word-refinement.md) 审查全部结构单位；无明确增益返回 `NO_CHANGE`。`REFINED` 每单位最多两个字符位置，原字必须参与候选比较；`REFLOW_REQUIRED` 回到当前完整句／联生成，不得在炼字响应里重写整句。每次采用后重新做全诗审校、逐单位炼字、内容与用典复检及最终红线。
10. 只导入实际 LLM 结果，完整诗的格律、内容复检和文学比较均由控制器安排。比较要覆盖整首诗，允许不改更好；采用后旧审校失效，必须完成新正文的全诗审校及最终验收。不要通过 PoemState 的最低 completed 接口绕过 Day 5。模型格式修复也计入预算，暂停保存完整 Polish 快照，不自行换 run_id 或扩大预算。
11. 最终出口前必须完成 redline：逐项判断是否把用户事实写反、把典故事实写反、为押韵牺牲本意，或使用了不合场景的器物、身份、语气。每项都要引用当前诗句并给出 yes/no/unknown；只有四项全为 no 才能继续，yes 或 unknown 均不得输出最终诗。仅 Polish.result 返回 completed 时输出诗题与正文，并显式标记 `quality_independently_verified: false`，说明流程证据齐全不等于文学质量已独立验证；用户需要时附简短且有依据的格律、用典说明。诗题独立于正文，不能充当必含内容。未完成时如实说明状态、原因与最后接受版本。内部候选、分数、修改和拒绝历史保留在快照中，默认不全部展示。确定性夹具只能验证流程，不能宣称真实模型文学质量已验证。

## 规则边界

首期覆盖常见标准近体诗格式，不覆盖词曲及完整拗救。韵库接入见 [韵律资源](references/rhyme-rules.md)。多音多韵先由 AI 在韵表已收录义项间作有依据的语境判读，再由规则引擎复检；无法可靠判读及未收录字保留为未知。重字按 `duplicate-review-3` 复核：程序只直接认可可验证的叠字和顶真位置；反复、专名、固定词语等语义性自述一律为 unknown，须有独立核验能力后才能支持。同一字符重复占据多个韵脚位由程序直接以重韵阻断。评分中第一组重字不扣分，第二组起每组扣 0.20（0–1 分制），由 Python 应用。重复意象和语义对仗仍交给文学评价。

当前只处理文字。图片输入仅预留封套，尚不读取或理解图片；图片依赖型请求需提供文字描述。后续适配设计见 [输入来源扩展](references/input-sources.md)，不将预留字段描述为已实现能力。
