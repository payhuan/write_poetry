---
name: write-poetry
description: 根据中文文字描述创作五言绝句、七言绝句、五言律诗或七言律诗，适用于主题、场景或指定情绪的近体诗创作。
---

# 赋诗

先规划全诗，再生成完整句或联，随后炼字。运行本 Skill 的 agent 负责语义理解、候选生成和文学评价，Python 负责确定性校验、规则过滤与状态记录，无需另配模型 API。

## 当前实现边界

目前提供输入提取与校验、PoemState 保存恢复、16 个模板的选择与绑定、Day 3 单轮候选、Day 4 全局 Top-K / Beam Search / 有限回溯，以及 Day 5 全诗审校、局部炼句和最终验收。状态 API 见 [输入与状态](references/state.md)，运行文件位置见 [独立工作区](references/runtime-workspace.md)。规则边界见 [检测引擎](references/constraint-engine.md)，单轮契约见 [候选与文学评分](references/poetry-round.md)，多路径接口见 [搜索控制](references/poetry-search.md)，最终阶段见 [全诗审校与润色](references/poetry-polish.md)。文学评价与语义判断由运行 Skill 的 LLM 提供；程序校验契约、证据归属和硬约束，不把 unknown 或未执行的检测报告为通过。

## 工作流

遇到用典、必含诗句或用户灵感时，先按 [内容要求](references/content-requirements.md) 提取 allusions 和 required_fragments，区分原样保留、允许润色及仅作灵感。用典名称按 [典故查询与规范化](references/allusion-lookup.md) 查询：唯一精确入口才能自动关联规范主典，歧义先消解，模糊结果不能自动改名，本地未命中不能解释为典故不存在。解析仅作结构和字数冲突检查，不宣称内容或格律已满足。Day 3 接入按 [内容安排与履行](references/round-content.md)：模型安排、典故引用和解释与 request 分开保存，不能回写用户原文依据。

1. 按 [输入解析](references/input-parsing.md) 理解自然语言并输出提取 JSON，分别记录诗体与 meter 中的起式、首句是否入韵、指定模板。保留原文依据、否定限制和待澄清问题；未指定与明确不入韵必须区分。
2. 使用 `scripts/parse_input.py --text-file request.txt --extraction extraction.json --output state.json` 校验并初始化 PoemState。使用 Python 3，脚本路径根据本 Skill 目录解析。需要澄清时先解决冲突，不进入生成流程。
3. 复核 [输入与状态](references/state.md) 中的原文、缺省决策和未提供依据的字段。未指定诗体时优先从指定模板确定，否则默认七言绝句。唯一匹配已自动绑定；部分诗格要求保留供规划补选。已有规范化 JSON 也可使用 `scripts/init_state.py --input request.json --output state.json`。
4. 按 [创作指导](references/composition-guide.md) 规划意象、各句或各联的作用与情绪推进。未绑定时按 [诗体说明](references/poem-types.md) 在兼容用户要求的模板中选择并调用 `poem_templates.py bind`，简短说明所选格式，再选择韵部。不要仅为可自行决定的未指定选项追问；明确要求冲突时则澄清。检测以绑定快照为准，未知文字规则返回 unknown。
5. 需要候选时读 [单轮流程](references/poetry-round.md)，调用 `poetry_round.py export` 导出当前句位上下文。韵脚句尚未选择韵部时先完成规划。有内容要求时先用 `plan` 导出安排，给未指定位置的必含片段和必用典故选择承担句位，通过 `export --content-plan` 校验；inspiration 不锁句位。本句有 locked_candidate 时跳过模型生成，直接 `filter` 检测原文。其余情况由 agent 生成完整候选句，再用 `filter` 校验、去重、检测格律和 exact 内容。ConstraintResult 的 `checks.reading.status=unknown` 时，按 [检测引擎](references/constraint-engine.md) 调用 `reading_disambiguation.make_request`，结合主题、上下句和各选项 senses 作语境判读；只能选择请求中已有的声部／韵部，不能为满足格律倒推读音。`checks.duplicate.status=unknown` 时调用 `duplicate_disambiguation.make_request`，逐字判断各重复位置的意群关系；仅 `reduplication`、`parallel`、`progressive` 可放行，普通复现、递减或牵强解释必须返回 `error`，证据不足返回 `unknown`。分别用对应模块的 `apply_review` 校验模型结果，再将 readings 或 duplicate_review 回送 Constraint Engine 复检；不能直接改写 checks。锁定原句与格律明确冲突时报告原因，请用户调整要求。
6. 有 `content_request.candidates` 时，agent 按内容契约逐项说明用典、adapt 或 inspiration 的实际表达，使用 `review-content` 导入。用典评价读取同项 `allusion_reference`：resolved 按主典和出处解释，ambiguous 不得判 satisfied，not_found 需另有可靠依据，否则留 unknown；不编造出处。必用典故和 adapt 的当前履行必须确认，不能以平均文学分代替。随后只对最新 `score_request.candidates` 评分；`finish` 验证、排序并原子记录候选和完整内容履行依据。每个模型阶段（生成、内容评价、文学评分）首次输出后最多修正格式两次，attempt 依次为 1、2、3；仍有错误即停止并报告。所有中间文件和最终状态包保存在独立运行工作区，不写入 Skill 安装目录。
7. 单轮仍只推荐。需要推进全诗时读 [搜索控制](references/poetry-search.md)，从已规划状态创建 Search，反复读取 next_job，按 stage 提供真实生成/内容评价/评分 JSON，再 submit 并保存完整运行。不要手动追加绕过控制器归属验证，也不要把没有模型结果的空循环描述为生成成功。全局路径数、重试和回溯均受共享预算限制。
8. ready_for_review 是 Day 4 交接状态，PoemState 仍为 reviewing。继续创作时读 [全诗审校与润色](references/poetry-polish.md)，从完整 Search 快照显式选择一个 path_id 创建 Polish，不默认取首稿，不把任意状态字典当已验收草稿。程序重新检测规则并核验当前内容证据；交接失败先报告原因，不能手动改状态或复用历史候选评分。
9. 按 next_job.stage 提供全诗 review、局部 generate、candidate_content、compare、final_content、redline 或 final JSON。审校从主题、语言、意象、结构、情绪/时间/视角、表达和律诗中间两联对仗出发，不机械套起承转合，不按词藻或典故名称计分。review 无问题可以直接进入内容复检和最终红线。问题必须引用具体句位；确需成联修改时审校要指出整联受影响的位置，候选包只修改这些位置。保留所有 exact 字句和指定位置；用户锁句在输入时应记录为 exact。若提高质量确实需要改变用户要求，报告 needs_clarification 并保留原稿。
10. 只导入实际 LLM 结果，完整诗的格律、内容复检和文学比较均由控制器安排。比较要覆盖整首诗，允许不改更好；采用后旧审校失效，必须完成新正文的全诗审校及最终验收。不要通过 PoemState 的最低 completed 接口绕过 Day 5。模型格式修复也计入预算，暂停保存完整 Polish 快照，不自行换 run_id 或扩大预算。
11. 最终出口前必须完成 redline：逐项判断是否把用户事实写反、把典故事实写反、为押韵牺牲本意，或使用了不合场景的器物、身份、语气。每项都要引用当前诗句并给出 yes/no/unknown；只有四项全为 no 才能继续，yes 或 unknown 均不得输出最终诗。仅 Polish.result 返回 completed 时输出诗题与正文；用户需要时附简短且有依据的格律、用典说明。诗题独立于正文，不能充当必含内容。未完成时如实说明状态、原因与最后接受版本。内部候选、分数、修改和拒绝历史保留在快照中，默认不全部展示。确定性夹具只能验证流程，不能宣称真实模型文学质量已验证。

## 规则边界

首期覆盖常见标准近体诗格式，不覆盖词曲及完整拗救。韵库接入见 [韵律资源](references/rhyme-rules.md)。多音多韵先由 AI 在韵表已收录义项间作有依据的语境判读，再由规则引擎复检；无法可靠判读及未收录字保留为未知。重字默认阻断并交由 AI 作绑定语境判断；只允许叠字、明确并列意群或明确递进意群，其他重复为错误。重复意象和语义对仗仍交给文学评价。

当前只处理文字。图片输入仅预留封套，尚不读取或理解图片；图片依赖型请求需提供文字描述。后续适配设计见 [输入来源扩展](references/input-sources.md)，不将预留字段描述为已实现能力。
