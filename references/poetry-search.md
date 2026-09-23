# Day 4：Top-K、Beam Search 与有限回溯

## 入口与交接

`scripts/poetry_search.py` 的 `Search` 把 Day 3 句／联流程连成可暂停的多路径搜索。运行 Skill 的 agent 提供模型 JSON，不要求模型 API。支持五绝、七绝、五律、七律；律诗颔联、颈联按整联推进，其余位置按句推进。

开始前完成模板、韵部、[全诗语义规划](poem-planning.md)与内容安排。`Search.create(state, poem_plan=poem_plan, content_plan=content_plan)` 中 poem_plan 为新运行必需参数。已有完整初稿可作为不可删除前缀播种：先把全部原句写入 PoemState，再以这个完整状态为依据重新生成并验证 PoemPlan 与内容安排；不能只改旧计划的 `binding`。固定正文不产生虚构的候选评分，初始分数保持 0。未定位的必需内容须先按 Day 3 plan 契约安排。搜索固定这些安排，只在追加正文后按当前 revision 重绑定同一组 assignments，不修改用户原文、指定句位或诗格。

完整路径在当前上下文调用实际 `engine.check_poem`，保存 checks、warnings 与 unknown；读音和全诗重字分别按整诗范围复核。随后对每句重跑确定性内容检查；仅对存在 `semantic_review_due` 的要求发语义工作（包括必需项以及被选作内容依据的可选典故或灵感项）。早先候选上的 satisfied 仅为历史，不作全诗证明。所有必需项必须在指定承担句位得到 satisfied；可选典故和 inspiration 不阻断。此处仅核对内容要求，无全诗文学评分、炼句或最终润色。

初稿播种前可选做一次规则预检：`python scripts/constraint_engine.py --state draft-state.json`。它能提前显示确定性格律问题及待复核项，但不会替代控制器中的 reading、duplicate 或语义复核。

输出 `ready_for_review` 时，`drafts[*].state.status` 为 `reviewing`，`quality_score` 不冒充全诗文学评价。绝不设置 completed。未知或失败的完整路径进入 diagnostics。

后续由 [Day 5 全诗审校控制器](poetry-polish.md) 接收完整 `{search, sha256}` 快照，调用者显式选择 `drafts` 中的 `path_id`。Day 5 重新核验规则与内容证据，不改变本 Search 的状态或搜索算法；只有 Day 5 的最终检查通过后才可输出 completed。

按当前层稳定排名检查完整路径，取得第一条合格草稿即交接，其他路径保留在运行中，不为收集更多草稿额外消耗模型预算。

## Python 接口与工作封套

```python
from poetry_search import Search

search = Search.create(state, poem_plan=poem_plan, content_plan=content_plan)
job = search.next_compact_job()  # 紧凑模型视图；重复读取不计费、不生成新批次
# 模型工作推荐导出紧凑视图：python scripts/poetry_search.py next --input examples/search-01.json --output examples/job-01.json --compact
# 紧凑视图包含 job.context 与当前 stage 的 request；控制器内部仍保存完整过滤工件。默认 next 输出和 Python next_job() 保持完整兼容。
# Python 调用也可用 search.next_compact_job()；响应仍只需照抄原工作身份字段并提交。
response = {k: job[k] for k in
            ('run_id', 'job_id', 'path_id', 'batch_id', 'context_sha256')}
response['payload'] = agent_json  # Day 3 契约；attempt 必须为 job.attempt
search.submit(response)
search.save('examples/search-01.json')
search = Search.load('examples/search-01.json')
```

普通句 stage 为 generate → 可选 reading → 可选 duplicate → 可选 content → score；律诗中间联为 unit_generate → 可选 unit_reading → 可选 unit_duplicate → 可选 unit_content → unit_score。读音和重字复核只接收控制器生成的范围绑定请求，候选生成结果仍禁止携带相应证明。两项规则复核完成后才可进入内容语义或评分。确认的全诗重字证明仅适用于原正文；句位候选、联内逐句候选和完整初稿按各自检查范围独立复核。Search 从已有完整正文启动时，若检查出现重字 unknown，则发出 poem_duplicate。完整出口的内容行还可能依次发出 final_reading、final_duplicate、final_content。改稿或正文变化会使旧证明失效，必须对新文本重审。整联候选和 `literary-5` 逐字、逐结构双层对仗评价见[完整句／联生成](unit-generation.md)。每个新阶段有新 job_id；格式修复保持 job_id，attempt 递增。不得在没有实际模型响应时填充评分或运行空生成循环。

模型输出尚未解析时，使用 `payload={"attempt": job.attempt, "raw_json": 原始模型字符串}`，控制器使用 Day 3 严格解析器解析，语法错误同样进入有限格式修复计数。attempt 区分新修复响应和重复导入，即使模型再次返回完全相同的坏 JSON 也能有界结束。外层工作封套由调用者构造，必须是合法 JSON；不要在控制器外无限修复模型输出。

固定 exact 完整句跳过生成模型交互，直接检查用户原文；仍需文学评分才能采用。固定句与规则明确冲突进入 needs_clarification。初始正文若与固定原文不同，完整出口保守拒绝，保留 FINAL_LOCKED_TEXT_MISMATCH，不擅改原句。

`submit` 先验证工作归属，然后在独立运行副本上过滤、评分和 fork，全部通过才发布。普通句使用 append_line；中间联两句全部通过后使用 append_unit，一次 revision 原子发布。任一句失败、内容未知或响应格式错误都不留下半联。`REPLAN_REQUIRED` 停在 needs_clarification 并保存冲突，不擅自改规划。

成功工作重复导入返回 already_processed；改变已处理结果报 CHANGED_REPLAY。已拒绝的相同 payload 重复导入返回 already_rejected，也不计费。旧工作不能转用于新路径，即使两者 revision 相同。摘要用于防误用和文件损坏检测，不是签名；恢复文件应来自可信本地运行。

## 搜索、排名与检查点

同深度所有活跃父路径依次获得一个扩展批次，完成这一层后汇总全部合格子路径，统一保留 beam_width 条。排分取该路径本次运行所有采用句／联的分数算术平均；普通句兼容 literary-1，中间联使用 literary-5，对仗低分只影响排序。固定初始正文不虚构评分。

主要排名仅限同深度。恢复库先按深度降序选择最近检查点，同一深度才按平均分和创建序号排序。当前实现不合并路径，因此不会仅凭相同正文错误合并不同模板、韵律或内容安排的分支。

被全局剪枝的合格子路径保存在 alternatives。每条包含独立 PoemState、采用候选、原始报告及完整父状态；这是已经验证、尚未继续扩展的分支。恢复直接取出整个检查点，不删除最后一句，不使用被失败路径修改的内容状态。分支移出恢复库即表示已尝试，失败后进入 diagnostics，不重新插入库。

候选全失败/unknown、无合格评分、格式修复耗尽、最终规则或内容失败会耗尽当前路径。优先扩展其他活跃路径；整层无存活子路径才恢复备选。max_backtracks=0 完全禁用恢复。默认最多保存 30 条备选，超额按上述检查点优先序淘汰并记录 ALTERNATIVE_STORAGE_LIMIT。审计历史保留全部已发行工作和路径失败；长运行应选合适的预算，完整快照会随历史增长。

不预测典故语义可行性，不做关键词替代语义判断。不搜索不同内容安排；明确结构冲突由已有 Day 3 计划校验拒绝，后续 pending 不误拒绝当前句。既有固定前缀的必需内容在完整出口复核。

## 预算与状态

配置沿用 state.budget 的 candidates_per_round、beam_width、max_backtracks、max_rounds。额外参数 max_alternatives 默认 30，max_attempts 默认 3（1–3），max_interactions 默认 `max_rounds * 9 + line_count * 9`，可在 Python 创建接口显式设置。

| 消耗 | 计数单位 |
| --- | --- |
| used.rounds | 整个运行已开始的父路径扩展批次，每父路径一批；包括锁句直检，不因回溯重置 |
| used.interactions | 已导出的模型工作次数，生成、内容评价、评分、最终内容复核及每次格式修复各计一次；导出即占用额度，重复读取/导入不重扣 |
| used.backtracks | 从恢复库取出一次分支检查点 |

预算用尽保留 active、children、alternatives、pending 或最后 round_artifact 和结构化 events，不把部分诗标成功。不进行不计费的模型重试。程序内部规则复核不算模型调用。

| status | 含义 |
| --- | --- |
| waiting_for_llm | next_job 有工作，等待实际模型响应 |
| ready_for_review | 至少一条完整正文通过本阶段规则和内容检查 |
| budget_exhausted | 扩展或模型交互额度耗尽，保留当前有效进展 |
| no_feasible_path | 活跃路径耗尽，备选为空或回溯额度不允许恢复 |
| needs_clarification | 规划不足或锁定要求冲突，需要补齐规划/调整请求后新建运行 |
| run_error | 执行中不可恢复异常，保留运行与原因供诊断 |

内部 running 仅在处理事务时出现。终止状态不自行继续、不自动扩大预算。

## 保存恢复与 CLI

`to_dict/from_dict` 使用 `{search, sha256}` 完整包，版本 `poetry-search-7`。search 额外保存完整 `poem-plan-2`；路径保存 semantic_locks、已复检 readings 与整诗重字证明。`poetry-search-1` 至 `poetry-search-6` 可载入为 `legacy_readonly` 供审计，但 next_job 为空、submit 被拒绝；须基于原始状态建立新规划和新运行，不能把旧快照静默升级为可继续创作的证明。Day 3 旧过滤工件必须重新导出并复核。

保存原子发布新文件，禁止覆盖；CLI 相对数据路径按[独立工作区](runtime-workspace.md)解析，并禁止向 Skill 目录写入。整个项目可迁移，脚本定位内置资源相对于自身目录。

```sh
python scripts/poetry_search.py start --input examples/planned-state.json --poem-plan examples/poem-plan.json --content-plan examples/content-plan.json --output examples/search-01.json
python scripts/poetry_search.py next --input examples/search-01.json --output examples/job-01.json --compact
# agent 写 response-01.json（工作身份 + payload）
python scripts/poetry_search.py submit --input examples/search-01.json --response examples/response-01.json --output examples/search-02.json
```

没有内容要求可省略 --content-plan。CLI start 输入为纯 PoemState 字典。next 导出的工作是提示材料，恢复始终读取完整 search 快照。

## 确定性演示与测试

```sh
python scripts/day4_example.py --output examples/day4-result-new.json
python -m unittest discover -s scripts -p 'test_*.py'
```

演示使用项目内微型声韵夹具及固定模型响应：第一句高分支 → 第二句生成无效候选 → 活跃路径耗尽 → 恢复第一句低分支 → 四句完整规则通过 → reviewing。预期 rounds=5、backtracks=1。这些重复字串仅测试控制逻辑，不是文学作品。无真实模型调用。

测试覆盖四种诗体、全局剪枝、存活路径优先、备选恢复、零回溯、共享预算、格式修复上限、跨路径拒绝、数据独立、固定要求、完整内容 unknown 拒绝、全诗规则重检、保存恢复一致、幂等、稳定同分、事务原子性和迁移后从其他工作目录运行。

2026-09-15 验证：原有 102 项测试先行通过；加入 27 项控制器测试后共 129 项通过。保存的演示为 `examples/day4-result.json`，可通过 Search.load 恢复查看完整历史、剪枝、恢复原因与实际检测结果。
