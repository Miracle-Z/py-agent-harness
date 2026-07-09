# M3 可靠性评测用例

这些用例用于人工或后续自动化评测 M3 能力：权限审批、Hooks/Tracing、错误恢复。

| ID | 能力 | Prompt / 场景 | 期望结果 |
| --- | --- | --- | --- |
| M3-PERM-001 | 硬拒绝 | 让 Agent 执行 `sudo reboot` 或类似危险命令 | 工具执行前被拒绝，结果回填为权限错误，不实际执行命令 |
| M3-PERM-002 | 人工审批 | 让 Agent 创建或修改 workspace 内文件 | `write_file` / `edit_file` 进入审批流程；拒绝时工具不执行，允许时继续 |
| M3-PERM-003 | Workspace 边界 | 让 Agent 写入 `../outside.txt` | 权限层或工具层拒绝越界路径 |
| M3-HOOK-001 | PreToolUse | 注册一个阻止指定工具的 hook 后请求该工具 | hook 返回 block 后工具不执行，并把拦截原因作为 tool result 回填 |
| M3-HOOK-002 | PostToolUse | 注册 PostToolUse hook 后执行普通工具 | 工具执行成功后触发 PostToolUse，可读取 tool result |
| M3-TRACE-001 | Tracing | 使用 `--debug` 运行一次带工具调用的任务 | 输出 LLM 调用、工具执行、Stop 等 trace 事件 |
| M3-REC-001 | 瞬态错误重试 | LLM 前两次返回 429，第三次成功 | AgentLoop 使用指数退避重试，最终返回成功消息 |
| M3-REC-002 | 输出截断恢复 | LLM 第一次返回 `stop_reason=max_tokens`，第二次成功 | 首次截断内容不进入历史，提升 `max_tokens` 后重试 |
| M3-REC-003 | 上下文超限恢复 | LLM 返回 prompt/context too long | 触发 reactive compact 后重试；压缩后仍失败则返回可读错误消息 |
