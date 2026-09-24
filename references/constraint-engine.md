# 纯规则格律检测

当前仅支持平水韵、五绝／七绝／五律／七律，共 16 个绑定模板。只判断文字位置与声韵事实，不读取含义，不做语义对仗、意象、文学评分、生成或搜索。

## 接口

将项目 scripts 目录加入 Python 模块搜索路径：

```python
from constraint_engine import check_candidate, check_poem

result = check_candidate(
    {"id": "candidate-1", "text": "东东月月东", "line_index": 2}, state,
    readings={"2:5": {"section": "上平声部", "group": "一东"}},
    duplicate_review=validated_duplicate_review,
)
result = check_poem(state)
```

示例仅展示接口，不是文学作品。state 接受 PoemState 或可恢复的状态字典；返回 JSON 字典 ConstraintResult。候选严格要求 id、text、line_index 三个字段。句号、字位从 1 开始；候选按指定句位检查，重复统计时替换原句，也支持尚未写到的合法句位。全诗逐项检查 lines 数组，每个元素必须是一句；缺句是检测失败，超出 PoemState 容量的损坏状态是调用错误。

检查不修改状态、不保存结果，返回当前 revision。调用者可通过 set_assessment 或 evaluate_candidate 保存；正文或规划变更后旧结果仍被拒绝。字数和句数来自 form，格律只读 meter_template.data 快照；没有快照返回 TEMPLATE_UNBOUND，不凭 template_id 重查当前库。已有快照的状态恢复也不重新检查当前模板库。

命令行（路径相对当前目录传入，内置资源始终相对模块加载）：

```sh
python scripts/constraint_engine.py --state state.json
python scripts/constraint_engine.py --state state.json --candidate candidate.json --readings readings.json --duplicate-review duplicate-review.json
python -m unittest discover -s scripts -p 'test_*.py'
```

stdout 为 UTF-8 JSON；调用错误退出码 2。检测失败或未知是正常结果，不作为调用异常。ConstraintEngine(lexicon=RhymeLexicon(data)) 支持注入小型可控测试韵库。

## 文本和位置

保留 original、规范化 text 及每字 cells。offset 是原始句字符串中从 0 开始的 Unicode 码点位置，不是 UTF-16 单元或 UTF-8 字节位置；pos 是去除约定标点、空白后的字位。非 BMP 汉字占一个位置。不做 Unicode 合并、简繁转换或自动纠字。

允许忽略 Unicode 空白及 `，。！？；：、,.!?;:「」『』“”‘’（）()《》〈〉【】`。逗号、句号、问叹号、分号和换行划分句段；同一数组元素含多个有正文的句段报告 LINE_SEGMENTS，不能拼成一句通过。纯标点文本报告长度／句段失败；空白或空字符串属于非法调用。数字、拉丁字母、表情、未列出的符号保留在位置表中并报告 INVALID_CHARACTER。字数是保留下来的码点数，非法字符同时阻止 length 通过。

## 声韵依据与不确定性

反向索引保留每字实际收录的多个 `(section, group)`，平仄从该记录的声部导出：上平、下平为平，上、去、入为仄。没有拼音与韵部的对应数据；不接入独立 Word_Tune 或现代拼音表，不组合出虚假读音。

readings 使用 JSON 键 `句号:字位`，如 `2:3`；坐标必须存在于替换后的正文上下文。值为用户明确给出的声部／韵部选择器（section、group 至少一项），只能筛选该字确实存在的记录。未收录选择器返回 READING_NOT_ATTESTED；传入非空拼音字符串返回 READING_MAPPING_UNAVAILABLE，不用普通话声调推断古音。平仄、整句规则、押韵使用同一份筛选结果。

一个字存在多个声部／韵部归属且调用者未给出唯一选择时，只有它可能改变当前规则结论才新增必需项 `checks.reading=unknown`。若所有归属平仄相同且该字不在押韵位置，各选项对本次格律与押韵判断等价，引擎直接按共同平仄继续，并在 `checks.reading.equivalent` 记录坐标、全部选项及 `same_tone_non_rhyme` 原因；这不是语义上的读音确认。其余情形的每个 `READING_REVIEW_REQUIRED` 问题返回 coordinate、句号、字位、原文 offset、字符、所在句和全部选项；选项包含 section、group、tone、senses。`checks.reading.context` 同时提供主题、情绪和当前诗句，供语境判断。senses 为空只表示数据源没有提供释义，不表示可以按格律任选；数据没有独立拼音字段，因此这些选项应称为平水韵归属或读音义项，不能包装成臆造的现代拼音。

使用 `reading_disambiguation.make_request(result)` 生成与原检测绑定的 AI 请求。模型对每个位置只能返回 selected 或 unknown；selected 必须逐字匹配请求中已有的 section/group，并说明语境理由。`apply_review(request, payload)` 校验请求摘要、句位、字符和选项后生成 readings；`recheck_candidate` / `recheck_poem` 会把它交回 Constraint Engine。AI 只负责选择语义相符的已收录归属，最终平仄和押韵仍由规则引擎判定；不得因某选项能通过格律就选择它。任一位置为 unknown 时不生成选择，复检仍为 unknown。

多归属已经由唯一 section/group 选择消歧后，`checks.reading=pass` 并在 selected 中记录实际归属和 senses。缺字为 CHAR_NOT_FOUND；即使位于“中”，仍报告资料不足。“中”只免除基础声调限制。已选韵部不会反向用作读音选择依据。简繁原字分别查表，未收录不擅自转换。

## 重字语义复核

引擎在候选替换后的完整上下文或全诗中统计每个汉字的位置。没有重字时 `checks.duplicate=pass`；发现重字而未提供审查时，该必需项为 `unknown`，并为每组返回 `DUPLICATE_REVIEW_REQUIRED`、char、count 和全部 line/pos/offset。规则引擎不自行猜测词义，也不会把重字直接当作普通 warning 放行。

重字复核契约为 `duplicate-review-3`。请求按每个重复字符提供全部句位、字位、原文及对应句子。回复每组必须提交 `device`、具体理由，以及覆盖每个位置的 `position_evidence`（句位、字位、完整原句和该处词义／作用）。为防止模型仅靠自述为不当重字辩护，程序只放行可机械验证的两种形式：叠字须让该组每个字位都参与同句相邻的重复；顶真／联珠须让每个字位都参与前句末字与紧邻后句首字的对应。其他类别——包括反复／回环、句内对称、专名、固定词语及其他修辞——现阶段统一降为 `unknown`，因为项目没有可独立核实这些语义主张的证据库或人工复核接口。它们不得因理由写得充分而直接通过。无意同义重复、含糊解释、律诗中间两联的无意复现、上下联同一对应位置同字均应判错；证据不足保持 `unknown`。仅称意群并列或递进不能通过。

同一汉字出现在两个或更多押韵句的韵脚位置时，引擎以 `DUPLICATE_RHYME_WORD` 直接判错，不生成可覆盖它的复核请求。任一 `error` 使 duplicate=fail；任一 `unknown` 保持整体验证未知；只有上述两类机械验证通过的形式可使 duplicate=pass。规则复检会重新验证类别与具体位置，不信任模型可控的证明字段；旧复核证明一律不能用于新规则。题目和序言不属于 PoemState 的诗句，不计入检测。`ConstraintEngine(..., duplicate_policy='report')` 仅供机械规则夹具使用，正常 Skill 工作流不得使用。

评分还会按整首诗中不同重字组的数量扣分：第一组不扣分；第二组起每多一组从模型加权总分扣 `0.20`，最低为 `0`。同一个字无论出现两次还是更多次，均算一组。该扣分由 Python 在规则复核通过后统一应用，模型不能用理由抵消；重韵仍是硬性失败，不进入评分。

韵脚句位完全来自快照 rhyme.positions，首句不重新猜测。非韵脚候选为 not_applicable。韵部支持规范名（如 `十一真`）、`上平声部/十一真`，以及省略序号的尾名别名（如 `真`）。别名唯一匹配时规范化为完整韵部名；多个匹配返回 RHYME_GROUP_AMBIGUOUS 及 choices，必须消歧，不合并判定；无匹配返回 RHYME_GROUP_UNAVAILABLE。简繁不猜测。未选择韵部和其他韵书返回 unknown。韵脚必须是正确长度单句的末字，否则 RHYME_POSITION_UNAVAILABLE。

## 附加规则口径：pingshui-basic-1

四种诗体每句都进入同一组规则，无论模板 constraints 是否列出。规则是否适用由具体句式决定，不由诗体决定。文字规则映射为：忌孤平 → guping-wangli-1；忌三平调 → last-three-level-1；二四六分明 → bound-even-positions-1（由逐字固定位置检查执行）。快照中其他文字规则返回 UNSUPPORTED_RULE，不因识别到名称就通过。

采用王力《诗词格律概要》第二章第三节的狭义孤平口径：检查五言平平仄仄平这一句型，七言检查其末五字对应部分；确定出现仄平仄仄平报告 GUPING。不是把任何“仄平仄”都视为孤平，也不对仄收句按全句平声数量判孤平。参见[王力原著文字转载](https://www.99csw.com/book/1822/50868.htm)。

三平调检查所有句子的最后三个字；确定连续三平为失败，可能三平则未知，句中连续三平不属于本项。此为本阶段标准近体诗禁忌口径，不声称涵盖全部历史变例。依据与变例讨论见[蔡振念《王力五言律诗两种格式补证》](https://chinese.ncku.edu.tw/var/file/142/1142/img/1450/2004.pdf)。

疑似孤平本句自救（该句型末五字仄平平仄平）返回 UNRESOLVED_GUPING，不判为孤平失败，也不宣称已支持拗救。其余变格、对句相救体系没有实现；基础模板不符仍如实记录 TONE_MISMATCH，这只表示不合绑定标准，不是断言古诗无此格。显式要求但未实现的规则返回 unknown。当前不接受自动放宽模板或抵消固定位置失败。

整句规则枚举每个位置的可能平仄（每句最多 128 个组合），没有选择有利组合。缺失声调只为规则的不确定性分析表示为两种可能，不作为补全的查表结果。所有可能都违规才报规则失败；部分违规、部分通过或涉及未支持自救时未知。

## 结果和兼容

顶层保留 revision、valid、checks、errors、warnings，并新增 binding_version、scope、subject、context_sha256。正常模式下 length、pingze、rhyme、reading、duplicate 均为必需项。必需项 fail → valid=false；必需项 unknown/not_run → null；其余 → true。旧结果省略 required 仍按 true；required 只接受布尔值，不能将核心检查降级。非韵脚候选的 rhyme=not_applicable 不阻断。

对象绑定由 scripts/assessment_binding.py 统一生成和校验，binding_version=1：

- scope=poem：subject 为 `{text_sha256: 摘要}`，摘要覆盖原始 lines 字符串数组，保留句界、标点和空白。
- scope=candidate：subject 为 `{candidate_id: ID, line_index: 句位, text_sha256: 摘要}`，摘要覆盖候选原始 text 字符串。相同正文但不同 ID 或句位也不能复用。
- context_sha256 覆盖检查前的完整正文（不是候选替换后的正文）、request、poem_type、form、template_id、完整 meter_template 快照、rhyme、constraints、theme、emotion、imagery、structure。候选登记、评估历史、评分、状态阶段和预算不进入摘要，避免记录结果本身使上下文失效。

摘要算法为 SHA-256；先将对应 JSON 值按键排序、无额外空白、保留 Unicode、禁止非有限数序列化，再用 UTF-8 编码。不做正文规范化；原文细节变化也需重检。revision 继续单独校验。摘要用于避免结果误用，并非对外部任意构造结果的真实性签名；状态模块不代替引擎重新计算规则。

set_assessment 只接收匹配当前全诗和上下文的新结果；evaluate_candidate 只接收匹配指定候选 ID、句位、原文和当前上下文的新结果，pending/rejected 也不能写入错对象的结果。set_status('completed') 再次要求匹配当前全诗的绑定结果。绑定不匹配时抛 ValueError，状态保持不变。同 revision 的分支只要正文、模板或韵律／约束不同，就不能交叉使用结果。

保持状态版本 0.3.0 的读取兼容性：四个绑定字段全部缺失才视为旧结果，仍可读取、保存，并保留已有 retained/completed 历史标签；部分缺失或格式错误不当作旧结果。缺少绑定的结果不能通过上述写入入口重新使用，也不能使 reviewing 新晋级为 completed，必须重检。恢复新格式状态时校验当前结果的对象及上下文；较早 revision 的候选历史只校验对象绑定和摘要格式，不拿当前上下文去否定历史记录。

## 数据边界和验证

韵库和 SHA-256 位于 data/rhyme。当前文件是用户提供并要求替换的扩充生僻字重新抓取结果，共 106 部、18032 条记录；source.json 保存接收日期、摘要、统计及已知来源边界，不记录项目外本地路径。运行只加载这一份项目内韵表并校验摘要，不联网、不混入旧表，也不推定其他字音。所有 pass 都仅表示在当前资料、绑定模板和上述规则范围内通过，不是完整文献音韵证明。

测试用人工可控字集验证全 16 模板、固定位置、各类未知、逐位置消歧、孤平／三平调、“中”、重字语义绑定、重复替换、快照与 revision；真实韵库测试多归属、入声、文件摘要、CLI 与项目外工作目录加载。测试不依赖生成诗句质量。

对象绑定回归覆盖：候选结果写入错误全诗、不同候选交叉评估、同 revision 分支在正文／韵部／约束／模板快照上的交叉写入、原文标点变化、旧结果读取与晋级拒绝，以及合法完成与历史结果恢复。



