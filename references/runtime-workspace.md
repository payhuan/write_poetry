# 独立运行工作区

Skill 目录只保存版本化的代码、数据、文档、测试和静态示例。用户需求、模型请求与响应、PoemState、Search/Polish 快照、抓取缓存和最终结果都属于可变运行产物，默认写入 Skill 目录之外。

Windows 默认工作区为 `%LOCALAPPDATA%\write-poetry`；其他平台优先使用 `$XDG_STATE_HOME/write-poetry`，否则使用 `~/.local/state/write-poetry`。可通过环境变量 `WRITE_POETRY_WORKDIR` 设置长期位置，或在 CLI 使用 `--work-dir PATH` 为本次运行指定位置。工作区若位于 Skill 目录内会被拒绝。

CLI 的相对输出路径以工作区为根。例如 `--output states/a.json` 会写入 `<工作区>/states/a.json`，并自动创建父目录；已有目标仍不覆盖。相对输入先查工作区，再查项目外当前目录，最后只读查找 Skill 内静态资源。绝对输入可直接读取；绝对输出若落入 Skill 目录会被拒绝。

体验程序把每次运行放在 `<工作区>/runs/<时间戳>-<短ID>`。可执行：

```powershell
python -X utf8 try_poetry.py --work-dir D:\poetry-work --prompt "写一首春日山居五绝"
```

运行工作区不随 Skill 迁移，也不应提交到 Skill 仓库。需要备份或迁移创作记录时，单独复制工作区即可。
