# Py Agent Harness

一个面向学习与实践的 Python Agent 工程项目。

我们将从零实现一个 Claude Code 风格的 Coding Agent，理解 Agent Loop、工具调用、权限控制、上下文管理与可观测性等核心机制，并在此基础上逐步构建能够排查微服务故障的 Agent。

## 学习参考

本项目参考 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code/tree/main) 的学习路径进行实践。该项目以 Claude Code 风格的 Agent Harness 为主线，从 Agent Loop、Tool Use、权限控制、Hooks、上下文压缩、Memory、Subagent 到 MCP 等机制逐步展开。

本仓库不会直接照搬实现，而是使用 Python 工程结构重新实现核心概念，并在后续扩展到微服务故障诊断场景。

## 项目目标

- 不依赖成熟 Agent 框架，从底层理解 Agent 的运行机制。
- 构建一个能够读取、搜索、修改代码并执行命令的 Coding Agent。
- 建立测试、评测、权限控制和执行追踪等工程能力。
- 接入日志、指标、调用链和代码仓库，构建微服务故障诊断 Agent。
- 通过 GitHub Issues、Pull Requests 和文档记录团队学习过程。

## 项目功能

### 通用 Agent Harness

- Agent Loop 与多轮消息管理
- 多模型统一调用接口
- Tool 注册、参数校验与执行
- 权限审批、超时、重试与错误恢复
- Session、上下文压缩与 Memory
- 执行轨迹、日志、评测与成本统计

### Coding Agent

- 读取、搜索和修改代码
- 执行受限制的 Shell 命令
- 查看 Git Diff 并运行测试
- 根据任务制定计划并验证结果

### 微服务故障诊断 Agent

- 查询应用日志、监控指标与调用链
- 分析异常服务和最近代码变更
- 提出并验证根因假设
- 生成包含证据、根因和修复建议的诊断报告
- 在人工审批后尝试修改代码并运行测试

## 里程碑

| 里程碑 | 目标 | 主要交付物 |
| --- | --- | --- |
| M0：工程初始化 | 建立可协作、可测试的 Python 项目 | 项目结构、代码规范、CI、协作约定 |
| M1：最小 Agent | 跑通模型与工具调用闭环 | Agent Loop、消息模型、LLM 接口 |
| M2：Coding Agent | 完成基础代码操作能力 | 文件工具、搜索工具、Shell 工具 |
| M3：可靠性建设 | 提升 Agent 的安全性和稳定性 | 权限、超时、重试、Hooks、Tracing |
| M4：长任务能力 | 支持复杂任务和持续会话 | Todo、Session、上下文压缩、Memory |
| M5：扩展能力 | 支持外部工具和任务委派 | MCP、Subagent、后台任务 |
| M6：故障诊断 Agent | 完成微服务故障诊断闭环 | 日志、指标、Trace 工具与诊断报告 |
| M7：诊断与修复 | 在审批和隔离环境中验证修复 | 代码修改、自动测试、Git Diff |

详细计划见 [docs/roadmap.md](docs/roadmap.md)。

## 学习章节映射

项目阶段与 `learn-claude-code` 章节的对应关系如下：

| 项目阶段 | 对应章节 | 说明 |
| --- | --- | --- |
| M0：工程初始化 | 无直接章节 | CI、Ruff、Mypy、Pytest、协作规范是 Python 工程化底座，`learn-claude-code` 主要讲 Harness 机制。 |
| M1：最小 Agent | `s01_agent_loop` | Agent Loop、messages、LLM 调用、工具结果回填。当前项目的 AgentLoop 已对应这一章。 |
| M2：Coding Agent | `s02_tool_use` | 工具注册、工具分发、文件读写改、glob/search、shell、git diff。当前项目基本已完成，并比 `s02` 更工程化。 |
| M3：可靠性建设 | `s03_permission`、`s04_hooks`、`s11_error_recovery` | 权限审批、Hook 扩展点、错误分类、重试、恢复。Tracing 可以放在 Hooks 体系里做。 |
| M4：长任务能力 | `s05_todo_write`、`s08_context_compact`、`s09_memory`、`s10_system_prompt`、`s12_task_system` | Todo、Session、上下文压缩、Memory、运行时组装 system prompt、持久化任务图。 |
| M5：扩展能力 | `s06_subagent`、`s13_background_tasks`、`s15_agent_teams`、`s16_team_protocols`、`s17_autonomous_agents`、`s18_worktree_isolation`、`s19_mcp_plugin` | Subagent、后台任务、多 Agent 协作、协议、自主认领、worktree 隔离、MCP 外部工具。 |
| M6：微服务故障诊断 | `s07_skill_loading`、`s10_system_prompt`、`s12_task_system`、`s19_mcp_plugin`、`s20_comprehensive` | 诊断场景没有单独章节，需要复用知识加载、提示词组装、任务拆解、外部工具接入和综合 Agent 架构。 |
| M7：诊断与修复 | `s03_permission`、`s04_hooks`、`s11_error_recovery`、`s18_worktree_isolation`、`s20_comprehensive` | 人工审批、执行前后 Hook、失败恢复、隔离目录修复、最终集成闭环。 |

当前项目状态可以理解为：

- 已覆盖：`s01_agent_loop`、`s02_tool_use`、`s03_permission`、`s04_hooks`、`s05_todo_write`、`s08_context_compact`、`s09_memory`、`s10_system_prompt`、`s11_error_recovery`、`s12_task_system`
- 下一步重点：`s06_subagent`、`s13_background_tasks`、`s15_agent_teams`、`s16_team_protocols`、`s17_autonomous_agents`、`s18_worktree_isolation`、`s19_mcp_plugin`
- 微服务诊断阶段再引入：`s07_skill_loading`、`s19_mcp_plugin`、`s20_comprehensive`

## 项目结构

```text
src/
├── agent_harness/    # 通用 Agent 核心能力
├── coding_agent/     # Claude Code 风格 Coding Agent
└── incident_agent/   # 微服务故障诊断 Agent

tests/                # 单元测试与集成测试
evals/                # Agent 评测用例和结果
docs/                 # 架构、路线图与学习笔记
examples/             # 使用示例
playground/           # 实验代码
```

## 快速开始

项目要求安装 [uv](https://docs.astral.sh/uv/)。

```bash
git clone <repository-url>
cd py-agent-harness

uv sync
cp .env.example .env
# 编辑 .env，填入 API key 和模型名
uv run agent-harness
```

不带子命令时，`agent-harness` 会直接进入交互模式，行为接近 `learn-claude-code` 中运行脚本后进入问答循环的体验。

CLI 已接入真实 AgentLoop、Anthropic/OpenAI 模型适配和 Coding Agent 工具注册。运行前需要配置模型供应商的 API key 和模型名。

模型配置可以写在项目根目录的 `.env` 文件中。上面的 `cp .env.example .env` 会生成本地配置文件，随后填入自己的 key 和模型名。

Anthropic 配置：

```dotenv
ANTHROPIC_API_KEY=你的 Anthropic API key
ANTHROPIC_MODEL=claude-...
```

OpenAI 配置：

```dotenv
OPENAI_API_KEY=你的 OpenAI API key
OPENAI_MODEL=gpt-...

# 可选：OpenAI 兼容网关或代理地址；使用官方 OpenAI API 时留空
OPENAI_BASE_URL=
```

也可以不写 `.env`，直接在 shell 里 `export` 同名环境变量，或通过命令行传 `--api-key`、`--model`。

```bash
# Anthropic：进入交互模式
uv run agent-harness --model <anthropic-model-id> --root .

# Anthropic：一次性运行一个 Coding Agent 任务
uv run agent-harness chat --model <anthropic-model-id> --root . "Read README.md and summarize the project"

# 工具审批模式：ask 交互确认，allow 自动允许，deny 自动拒绝需审批操作
uv run agent-harness chat --approval-mode ask --model <anthropic-model-id> --root . "Edit README.md"

# OpenAI：使用 OPENAI_API_KEY / OPENAI_MODEL
uv run agent-harness --provider openai --root .

# OpenAI 兼容 endpoint
uv run agent-harness --provider openai --base-url <openai-compatible-base-url> --model <model-id> --root .

# 持久化并恢复会话；再次使用同一个 Session ID 会带回消息历史和 Todo
uv run agent-harness chat --model <model-id> --root . --session refactor-auth "Inspect the auth refactor"
uv run agent-harness chat --model <model-id> --root . --session refactor-auth "Continue the remaining work"
```

常用测试和质量检查：

```bash
# 运行全部测试；未配置外部 API key 时，集成测试会自动跳过
uv run pytest

# 只运行单元测试
uv run pytest tests/unit -vv

# 代码风格检查
uv run ruff check .

# 类型检查
uv run mypy src
```

## 协作方式

- 新功能和缺陷通过 GitHub Issue 跟踪。
- 每个改动通过独立分支和 Pull Request 提交。
- Pull Request 至少需要一位同伴 Review。
- 新功能应包含测试或评测用例。
- 重要架构决策记录在 `docs/adr/`。

## 当前状态

项目已完成 **M4：长任务能力**。当前已具备基础 Agent Loop、消息模型、LLM 接口、Tool 注册表、工具参数 schema、Anthropic/OpenAI Tool Calling 协议适配，以及从模型请求工具、本地执行工具、回传工具结果到生成最终回答的闭环。

M2 新增了 workspace 受限的代码操作工具：`read_file`、`write_file`、`edit_file`、`glob`、`search_text`、`shell`、`run_tests` 和 `git_diff`。这些工具可通过 `agent_harness.tools.create_coding_tool_registry()` 一次性注册到 AgentLoop。

M3 新增了权限审批、Hooks、Tracing 和错误恢复能力：

- `PermissionManager` 在 `PreToolUse` 阶段执行硬拒绝、规则匹配和人工审批；CLI 支持 `--approval-mode ask|allow|deny`。
- `HookManager` 提供 `UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`Stop` 等扩展点，AgentLoop 不直接硬编码权限和追踪逻辑。
- `InMemoryTracer` 通过 Hooks 记录 LLM 调用、工具执行、Stop 和 Error 事件；`--debug` 会打印 trace。
- `RecoveryManager` 支持 429/529 瞬态错误重试、`max_tokens` 截断恢复、prompt/context too long 后 reactive compact。
- M3 评测用例记录在 [evals/m3_reliability_cases.md](evals/m3_reliability_cases.md)。

M4 新增长任务状态与上下文治理能力：

- `TodoManager` 和 `todo_write` 使用整表替换语义维护当前 Session 的执行清单。
- `SessionStore` 将完整消息协议和 Todo 原子写入 `.sessions/`；CLI 通过 `--session <id>` 显式恢复。
- `ContextManager` 按“大结果落盘 → 协议成组裁剪 → 旧结果占位 → LLM 摘要”的顺序主动压缩，上下文超限恢复复用同一套配对规则；任何有损压缩前都会把完整 transcript 写入 `.transcripts/`。
- `MemoryStore` 使用 `.memory/*.md` 和稳定的 `MEMORY.md` 索引保存跨会话知识；索引进入 system prompt，按当前查询选出的最多 5 条正文作为不可信背景加入当前 user turn。
- `SystemPromptBuilder` 根据真实工作目录、已注册工具、Memory、Todo 和任务状态在每个用户轮次组装并替换唯一的 system message。
- `TaskStore` 使用 `.tasks/*.json` 保存依赖图，并通过 `create_task`、`list_tasks`、`get_task`、`claim_task`、`complete_task` 执行严格的状态转换。
- M4 评测用例记录在 [evals/m4_long_task_cases.md](evals/m4_long_task_cases.md)。

下一阶段进入 **M5：扩展能力**，重点是 MCP、Subagent 和后台任务。

## 当前流程图

当前主流程已经接通：

```text
uv run agent-harness
        |
        v
解析 CLI 参数与工作目录
        |
        v
创建 AnthropicClient/OpenAIClient + Coding Tool Registry
        |
        v
可选加载 .sessions/<id>.json
        |
        v
按工作区、工具、Memory、Todo、Task 组装 system prompt
        |
        v
AgentLoop
        |
        +--> ContextManager 主动压缩
        |       大结果落盘 -> 成组裁剪 -> 旧结果占位 -> 超阈值摘要
        |
        +--> PreLLMCall Hook / Tracing
        |
        +--> RecoveryManager 包裹 LLM.complete(messages, tools)
        |       429/529 重试、max_tokens 恢复、context too long compact
        |
        +--> 如果模型请求工具：
        |       PreToolUse Hook
        |       PermissionManager 审批
        |       ToolRegistry.execute(...)
        |       执行本地 Coding Tool
        |       PostToolUse Hook / Tracing
        |       将 tool result 追加回 messages
        |       回到 LLM.complete(...)
        |
        +--> 如果模型不再请求工具：
                Stop Hook
                可选保存 Session
                输出最终回答
```

已注册的 Coding Agent 工具：

| 工具 | 状态 | 作用 |
| --- | --- | --- |
| `list_files` | 已实现 | 列出 workspace 内目录 |
| `read_file` | 已实现 | 读取 UTF-8 文本文件 |
| `write_file` | 已实现，可审批 | 创建或覆盖文件 |
| `edit_file` | 已实现，可审批 | 精确替换一段文本 |
| `glob` | 已实现 | 按 glob pattern 查找文件 |
| `search_text` | 已实现 | 搜索文本或正则 |
| `shell` | 已实现，受限版，可审批 | 执行受限制命令 |
| `run_tests` | 已实现，自定义命令可审批 | 运行项目测试命令 |
| `git_diff` | 已实现 | 查看 Git diff |
| `todo_write` / `todo_read` | M4 已实现 | 替换或读取当前 Session 的 Todo |
| `compact` | M4 已实现 | 请求在下一轮模型调用前压缩上下文 |
| `memory_write` / `memory_read` / `memory_search` | M4 已实现 | 持久化和按需加载跨会话 Memory |
| `create_task` / `list_tasks` / `get_task` | M4 已实现 | 创建、列出和读取持久化任务图 |
| `claim_task` / `complete_task` | M4 已实现 | 按依赖认领任务并完成、解锁下游 |

后续能力入口：

| 能力 | 状态 | 对应里程碑 |
| --- | --- | --- |
| 配置校验与更友好的错误提示 | 部分实现 | M3 |
| 工具权限审批 | 已实现 | M3 |
| Hooks 与 Tracing | 已实现 | M3 |
| 超时、重试与错误恢复 | 已实现 | M3 |
| Todo、Session、上下文压缩、Memory、任务图 | 已实现 | M4 |
| MCP、Subagent、后台任务 | 未实现 | M5 |
| 日志、指标、Trace 诊断工具 | 未实现 | M6 |
| 隔离环境修复与人工审核 | 未实现 | M7 |

## 安全说明

本项目支持文件修改和受限制 Shell 命令执行。默认应限制 Agent 的工作目录和命令权限，危险操作必须经过人工审批。请勿在未隔离的生产环境中直接运行。

## License

项目许可证将在团队确认后补充。
