# Py Agent Harness

一个面向学习与实践的 Python Agent 工程项目。

我们将从零实现一个 Claude Code 风格的 Coding Agent，理解 Agent Loop、工具调用、权限控制、上下文管理与可观测性等核心机制，并在此基础上逐步构建能够排查微服务故障的 Agent。

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
uv run agent-harness
```

运行质量检查：

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

## 协作方式

- 新功能和缺陷通过 GitHub Issue 跟踪。
- 每个改动通过独立分支和 Pull Request 提交。
- Pull Request 至少需要一位同伴 Review。
- 新功能应包含测试或评测用例。
- 重要架构决策记录在 `docs/adr/`。

## 当前状态

项目处于 **M0：工程初始化** 阶段，核心功能正在逐步实现。

## 安全说明

本项目未来将支持文件修改和 Shell 命令执行。默认应限制 Agent 的工作目录和命令权限，危险操作必须经过人工审批。请勿在未隔离的生产环境中直接运行。

## License

项目许可证将在团队确认后补充。
