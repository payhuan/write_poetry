# 典故查询与规范化

项目内置 `data/allusions/allusions.jsonl`，用于离线查询主典、别名、相关人物、参考典故、简释和出处原文。来源与完整性摘要见同目录 `source.json`。运行时只读项目内文件，不依赖原始爬虫目录，也不联网。

该快照共有 1016 条典故，是局部语料。未命中只表示本地快照未收录，不能断言典故不存在。多数记录没有简释，此时出处原文仍是依据，但需要模型结合语境解释。数据来源说明仅称供个人研究使用，未提供再分发许可证。

## 命令行

从 Skill 目录运行：

```powershell
python scripts/allusion_lexicon.py resolve --query "王质观棋"
python scripts/allusion_lexicon.py search --query "王质" --limit 5
```

`resolve` 只接受主典名、别名或索引导航名的精确命中：

- `resolved`：只有一条记录命中，可使用 `record.canonical_name` 作为规范主典名；同时读取 `record_sha256`、简释和出处。
- `ambiguous`：入口词精确对应多条记录，必须根据用户意思从 choices 消歧，不能自动选第一条。
- `not_found`：没有精确命中；suggestions 只是模糊查询结果，不能自动规范化。需要用可靠外部资料核实，无法核实时保留 unknown。

`search` 支持对主典、别名、人物、参考典故、简释、出处书名和出处原文作子串查询。结果按精确入口、字段优先级和原始页序稳定排序。搜索结果用于发现候选；创作计划应保存用户要求 ID，规范信息保存在独立的 `allusion_references`，不得回写或覆盖用户原文。

## 创作约束

Day 3 导出上下文时，为每项 `request.allusions` 生成一条 `allusion_references`：

- 必用典故为 `ambiguous` 时，上下文状态为 `planning_required`，原因码为 `ALLUSION_DISAMBIGUATION_REQUIRED`。
- `resolved` 时，内容评价须根据规范主典、简释和出处解释候选句如何用典；只出现名称或相关字眼不能判 satisfied。
- `ambiguous` 不能判 satisfied。可选典故可以跳过，但若要使用仍须先消歧。
- `not_found` 不阻断创作，因为本地快照不完整；没有可靠补充依据时必须判 unknown，不能编造出处或含义。

`content_request` 会在用典要求中携带对应 `allusion_reference`。查到哪一条典故之后，按 [用典规范](allusion-usage.md) 完成事实门、映射门、质量记录和错误分类。这些引用与评价随候选内容证据进入 Day 5，终稿红线中的 `allusion_facts_reversed` 应同时核对当前诗句、用户要求和规范典故证据。任何事实方向相反或无法可靠判断的情况都不能通过最终出口。

程序只保证数据完整性、唯一精确解析、歧义阻断和证据归属；它不靠关键词自动宣称用典成功，也不替代模型的上下文语义判断。
