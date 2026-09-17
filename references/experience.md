# DeepSeek 命令行体验

进入项目目录后运行 `python -X utf8 try_poetry.py`，也可双击项目内的 `体验赋诗.cmd`。Python 3，无第三方依赖。还可直接运行 `python scripts/poetry_experience.py`。所有内置资源相对模块定位。

- `--prompt "写一首春日山居五绝"`：直接传入需求。
- `--check-api`：一次小型 API 连通测试。
- `--max-calls 60`：本次所有 API 调用的总上限，包括解析、规划和修复。
- `--work-dir D:\poetry-work`：本次运行工作区；也可设置 `WRITE_POETRY_WORKDIR`。

固定使用 deepseek-flash 和官方 Chat Completions 接口；不自动更换模型。读取 DEEPSEEK_API_KEY，Windows 当前进程缺少变量时会读取用户或系统持久环境变量。密钥不写日志。

运行会向 DeepSeek 发送用户需求、项目相关契约和创作上下文，使用账户 API 额度。默认单路径 Beam、最多 16 个搜索批次，润色最多两轮。整批候选均未通过时，适配层最多额外请求两次带错误反馈的重新生成；这计入 API 总调用上限，独立于控制器扩展批次计数。HTTP 错误和超时不自动重试。

运行记录保存在 [Skill 外部工作区](runtime-workspace.md) 的 `runs/<时间戳>-<短ID>`，包括实际请求（不含认证头）、模型响应、实际 token 数据、Search/Polish 快照及结果。运行材料含用户输入，请按需要自行保管。Ctrl+C 停止后保留最近快照；当前入口不提供自动断点续跑。

模型语义理解与文学判断不能由格式校验证明，完整格律或预算未通过时会显示失败或未完成草稿，不伪造 completed。API 文档：https://api-docs.deepseek.com/api/create-chat-completion/
