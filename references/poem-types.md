# 诗体结构

机器配置位于 [poem-forms.json](../scripts/poem-forms.json)。五绝、七绝为四句，五律、七律为八句；每句分别为五字或七字。绝句按句规划，律诗按联规划。

格律数据统一维护在项目内的 `data/shi_ge_lv.json`。四种诗体各有四种起式，共 16 个模板；支持 ID 和完整 pattern 选择。来源元数据见 `data/template-source.json`，项目运行不依赖外部原始文件。

V1 拟采用常见平声韵标准格式，偶数句押韵，首句是否入韵由具体模板决定；律诗中间两联纳入对仗评价。变格与完整拗救暂不覆盖。

## 选择与绑定

```text
python scripts/poem_templates.py list --poem-type 七言绝句
python scripts/poem_templates.py bind --state state.json --template-id qj_p_y --output planned.json
```

也可使用 `--pattern 平起首句入韵`。`--library` 为可选的全局参数，可在 list/bind 之前指定其他库路径。脚本及默认模板库按 Skill 目录定位；状态输入输出按[独立工作区](runtime-workspace.md)解析。

绑定会同时应用 request.meter 中的明确要求，不允许 CLI 选择覆盖用户要求。若状态中的要求已唯一确定模板，bind 可以省略 --template-id/--pattern；若仍有多个兼容模板则明确报错，须由规划补选。parse_request 已对唯一匹配自动绑定。

| 诗体 | 仄起不入韵 | 仄起入韵 | 平起不入韵 | 平起入韵 |
| --- | --- | --- | --- | --- |
| 五言绝句 | wj_z | wj_z_y | wj_p | wj_p_y |
| 七言绝句 | qj_z | qj_z_y | qj_p | qj_p_y |
| 五言律诗 | wl_z | wl_z_y | wl_p | wl_p_y |
| 七言律诗 | ql_z | ql_z_y | ql_p | ql_p_y |

用户明确指定起式时按要求选择。未指定时由创作规划选择并说明采用的格式，不必仅为该可选项询问用户。加载器不默认取第一项；歧义、ID 不存在或诗体冲突会报错。

Python 接口：`TemplateLibrary().select(poem_type, template_id=...)` 返回独立副本，`list(poem_type)` 返回逐句平仄及韵脚位置，`bind(state, template_id=...)` 绑定到 PoemState。绑定同步 template_id 和 first_line_rhymes，保留已选韵部；模板完整快照与原始库 SHA-256 写入 meter_template。更换模板使旧评分及检测失效。

`candidates(poem_type, meter=state.to_dict()["request"]["meter"])` 返回满足用户部分要求的完整模板副本。`select` 也接受 start 和 first_line_rhymes，仍要求唯一匹配。所有明确条件按交集筛选，不按字段优先级丢弃冲突。

## 数据检查与限制

加载时校验诗体、ID 唯一性、句数、总字数、逐字坐标完整性、平仄符号、句末标记、韵脚位置、首句入韵选项及偶数字位的交替、对与粘。

该校验证明数据结构与所检查的规则一致，不等于全面的诗学审定，也不代表生成诗句合律。原库未包含文献出处、版本和孤平等规则的形式化定义，暂按用户提供的模板使用。

`中` 表示该位置有平仄选择余地，但仍受句式整体约束。例如 `中平中仄平` 逐位展开可产生 `仄平仄仄平`；因此不能仅凭通配匹配判定通过。模板中的“忌孤平”“忌三平调”“二四六分明”仍保留在模板 constraints，当前引擎按 [版本化规则口径](constraint-engine.md) 执行，未实现的规则标记未知。与用户 constraints 分开保存，避免换模板后残留旧模板规则。
