# 项目路线图

## 当前方向

本项目先实现 Claude Code 风格的通用 Coding Agent，用于学习 Agent Harness 的核心机制；随后复用相同的 Agent 核心，增加日志、指标、调用链和代码分析工具，演进为微服务故障诊断与修复 Agent。

## 里程碑

### M0：工程初始化

- 完善项目文档、代码规范和协作约定
- 配置 Ruff、Mypy、Pytest 和 GitHub Actions
- 建立 Issue、Pull Request 和代码评审流程

### M1：最小 Agent

- 实现模型统一调用接口
- 实现 Agent Loop 和消息模型
- 支持一次完整的 Tool Calling 闭环

### M2：Coding Agent

- 实现文件读取、写入和搜索工具
- 实现受限制的 Shell 工具
- 支持运行测试并展示 Git Diff

### M3：可靠性建设

- 实现权限审批、超时和重试
- 记录 Agent 执行轨迹
- 建立基础评测集和回归测试

### M4：长任务能力

- 实现任务计划、Session 和上下文压缩
- 探索长期 Memory 与错误恢复

### M5：扩展能力

- 接入 MCP 工具
- 探索 Subagent 和后台任务

### M6：微服务故障诊断

- 查询和关联日志、指标与调用链
- 分析最近代码变更和历史故障知识
- 输出带有证据的根因分析报告

### M7：诊断与修复

- 在隔离环境中修改代码并运行测试
- 生成人工可审核的 Git Diff
- 统计诊断成功率、耗时和模型调用成本
