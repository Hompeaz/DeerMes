# DeerMes 当前架构报告

更新时间：2026-04-14
位置：`/home/hompeaz/code/deermes`

## 1. 总览

当前的 DeerMes 不是单一形态，而是一个由多种架构叠加出来的 agent 系统。它现在同时具备：

1. 分层架构（layered architecture）
2. 双运行时架构（single-agent / deerflow）
3. 工具注册表架构（tool registry + policy gate）
4. Provider 抽象架构（echo / ollama / openai-compatible）
5. 持久化学习架构（context / profile / memory / reflection）
6. 会话式交互架构（CLI one-shot + TUI session）
7. 配置驱动权限架构（permission profiles + approval flow）

从系统定位上看，DeerMes 现在更接近“可运行的 agent 内核 + 终端交互壳层”，还不是一个完整的网页产品。

## 2. 当前有哪些“架构”

### 2.1 分层架构

这是当前最核心的组织方式。主要层次如下：

- 入口层：CLI 与 TUI
- 会话层：消息、历史、session transcript
- runtime 层：运行时装配与执行主循环
- execution 层：planner / deerflow supervisor / reporter
- learning 层：context / profile / memory / reflection
- tools 层：filesystem / shell / registry / factory
- provider 层：echo / ollama / openai-compatible
- security 层：permission profiles / sandbox roots / approval
- persistence 层：JSONL session、memory、notes、logs、profile、permissions

### 2.2 双运行时架构

当前 DeerMes 有两条主运行路径：

1. `single-agent`
2. `deerflow`

`single-agent` 走单代理循环：
- bootstrap planner
- tool observations
- AgentLoop
- final synthesis

对应实现：
- `build_runtime()` in `src/deermes/runtime/app.py`
- `AgentLoop` in `src/deermes/runtime/loop.py`

`deerflow` 走轻量多角色路径：
- planner
- researcher
- synthesizer

对应实现：
- `build_deerflow_runtime()` in `src/deermes/runtime/deerflow_app.py`
- `DeerflowSupervisor` in `src/deermes/execution/deerflow/supervisor.py`

注意：它不是完整 DeerFlow 克隆，而是 DeerFlow 风格的最小多角色编排。

### 2.3 工具注册表架构

工具不是写死在主循环里的，而是通过 `ToolFactory -> ToolRegistry` 装配。

特点：
- 运行时按 `ToolSpec` 生成工具实例
- registry 统一暴露 `describe()` 和 `invoke()`
- DeerFlow researcher 可以拿工具子集 `subset()`
- 权限检查不在工具调用点分散实现，而是在 registry 统一拦截

这意味着 DeerMes 的工具系统已经是“可组合的能力层”，不是散落在 runtime 里的脚本调用。

### 2.4 Provider 抽象架构

模型提供方被抽象成统一接口：
- `ModelProvider.complete()`
- `ProviderResponse`

当前 provider：
- `echo`
- `ollama`
- `openai-compatible`

其中 `ollama` 现在不仅负责请求，还负责：
- 查询 `/api/tags`
- 查询 `/api/ps`
- 自动解析可用模型
- 在未显式指定模型时，选一个真实存在的本地模型

所以 provider 层已经不只是“HTTP 适配器”，而是“模型后端接入层”。

### 2.5 持久化学习架构

学习层是 Hermes 风格的核心借鉴点。当前包含 4 个子模块：

- `context`
  读取项目内上下文文件
- `profile`
  读取长期角色/人格/偏好配置
- `memory`
  存 reflection memory 并做简单检索
- `reflection`
  把一次 run 抽成后续可复用的记忆条目

现在的学习不是 embedding / vector DB，而是基于 JSONL + token overlap 的轻量实现。

### 2.6 会话式交互架构

当前有两种交互形态：

- one-shot CLI
- curses TUI

TUI 不是单纯终端输入框，而是带这些能力：
- session 持久化
- 多轮上下文回灌
- live trace
- approval flow
- provider/model/profile 切换
- raw output 查看

所以 DeerMes 已经从“只会跑一次命令”进化到“终端里的会话型 agent”。

### 2.7 配置驱动权限架构

权限不是硬编码在某个 if 里，而是配置驱动：
- `deermes.permissions.json`
- `PermissionManager`
- `PermissionProfile`
- `ApprovalRequest`

它控制：
- read roots
- write roots
- shell 是否可用
- shell allowlist
- 哪些动作必须审批

这是一套 agent 工具权限系统，不是操作系统级 RBAC。

## 3. 当前有哪些“层”

### 3.1 入口层

职责：接收用户输入，决定进入 run 还是 chat。

核心文件：
- `src/deermes/cli.py`
- `src/deermes/tui.py`

入口层负责：
- 解析命令行参数
- 选择 mode、provider、model、permission profile
- 初始化 runtime
- 在 TUI 中处理 `/provider`、`/model`、`/profile`、`/permissions` 等命令

### 3.2 会话层

职责：保存消息、构造最近上下文、抽取 assistant 最终文本。

核心文件：
- `src/deermes/chat/session.py`

会话层负责：
- `ChatMessage`
- `ChatSessionStore`
- session 名清洗
- 从 JSONL 读取/追加消息
- 从最近历史生成 `session_context`

### 3.3 配置层

职责：定义 agent 的基础配置和工具规格。

核心文件：
- `src/deermes/config/settings.py`

配置层当前负责：
- `AgentSettings`
- `ToolSpec`
- 默认工具集
- 默认 memory 路径
- context 文件名列表
- plan 步数上限

### 3.4 Runtime 层

职责：把 provider、tools、learning、execution 拼装成一个可运行 agent。

核心文件：
- `src/deermes/runtime/app.py`
- `src/deermes/runtime/deerflow_app.py`
- `src/deermes/runtime/loop.py`

runtime 层的边界是：
- 上接 CLI/TUI
- 下接 execution / learning / tools / providers / security

它是真正的系统装配层。

### 3.5 Execution 层

职责：定义 DeerMes 如何“思考和组织任务”。

核心文件：
- `src/deermes/execution/graph.py`
- `src/deermes/execution/planner.py`
- `src/deermes/execution/reporter.py`
- `src/deermes/execution/deerflow/roles.py`
- `src/deermes/execution/deerflow/supervisor.py`

当前 execution 层分两部分：

1. 通用执行骨架
- `ExecutionPlan`
- `ExecutionStep`
- `DeterministicPlanner`
- `Reporter`

2. DeerFlow 风格多角色骨架
- `planner`
- `researcher`
- `synthesizer`
- `DeerflowHandoff`
- `DeerflowPlannerBrief`

### 3.6 Learning 层

职责：让 agent 在项目和多轮运行之间积累状态。

核心文件：
- `src/deermes/learning/context.py`
- `src/deermes/learning/profile.py`
- `src/deermes/learning/memory.py`
- `src/deermes/learning/reflection.py`

当前四层功能是：

- context：自动读取 `AGENTS.md`、`SOUL.md`、`.cursorrules`
- profile：读取 `.deermes/profile.md` 与 `SOUL.md`
- memory：保存和检索 `MemoryEntry`
- reflection：把 run 的目标、观测、结果压成 reflection memory

### 3.7 Tools 层

职责：暴露可调用工具，并把工具能力标准化。

核心文件：
- `src/deermes/tools/base.py`
- `src/deermes/tools/factory.py`
- `src/deermes/tools/filesystem.py`
- `src/deermes/tools/shell.py`

当前工具有：
- `find_files`
- `read_file`
- `write_note`
- `shell`

工具层的关键特征：
- 每个工具先 `describe_invocation()`，再真正 `invoke()`
- registry 统一处理 unknown tool、permission denied、approval required、异常封装
- 工具错误不会直接炸掉执行链，而是转成 `tool_error[...]` observation 回给模型

### 3.8 Provider 层

职责：把 DeerMes 的模型请求统一映射到具体推理后端。

核心文件：
- `src/deermes/providers/base.py`
- `src/deermes/providers/echo.py`
- `src/deermes/providers/ollama.py`
- `src/deermes/providers/openai_compatible.py`
- `src/deermes/providers/__init__.py`

当前 provider 层能力：
- 抽象统一接口
- 支持模型列举 `list_models()`
- `ollama` 支持本地模型发现与 loaded 状态标记
- `build_provider()` 统一 provider 创建逻辑

### 3.9 Security 层

职责：决定工具调用是否允许、是否需要审批。

核心文件：
- `src/deermes/security.py`
- `deermes.permissions.json`

security 层负责：
- permission config 生成与加载
- profile 选择
- path sandbox
- shell allowlist
- approval request 生成
- prompt 中注入当前权限策略描述

### 3.10 Persistence 层

职责：把运行痕迹写盘。

当前主要持久化对象：
- `.deermes/sessions/*.jsonl`
- `.deermes/memory.jsonl`
- `.deermes/logs/*.log`
- `.deermes/profile.md`
- `.deermes/notes/*`
- `deermes.permissions.json`

这层目前主要是文件系统持久化，还没有数据库。

## 4. 当前主运行链路

### 4.1 one-shot run 链路

1. CLI 解析参数
2. runtime build
3. 加载 context / profile / memory / permissions
4. bootstrap planner 生成固定 plan
5. plan 中带工具的步骤先执行
6. 进入 `AgentLoop`
7. 模型返回 `tool` 或 `final`
8. tool observation 继续回灌
9. 产出 final response
10. 写 reflection memory

### 4.2 TUI chat 链路

1. curses UI 收集输入
2. 会话历史转成 `session_context`
3. 运行 runtime
4. trace 事件实时展示
5. 如果遇到审批请求，进入 `/approve` 或 `/deny`
6. run 完成后把 user / assistant / error / approval 都写进 session JSONL

### 4.3 deerflow 链路

1. bootstrap planner
2. planner 生成 structured brief
3. researcher 用受限工具子集收集证据
4. synthesizer 基于 handoff 和 observations 生成最终答复

## 5. 当前系统的分层边界是否清晰

整体上是清晰的，尤其这几条边界已经比较明确：

- UI 不直接碰工具执行细节，交给 runtime
- runtime 不直接写 shell/file 权限逻辑，交给 security + tool registry
- execution 负责“怎么组织任务”，learning 负责“怎么保留状态”
- provider 负责“怎么请求模型”，不掺进上层会话逻辑

这说明 DeerMes 当前已经不是 demo 脚本，而是有明确模块边界的 agent scaffold。

## 6. 当前仍然存在的架构限制

### 6.1 planner 还偏脚手架

`DeterministicPlanner` 目前仍是固定三步，不是真正按任务动态规划。

### 6.2 deerflow 还是最小多角色版

当前只有：
- planner
- researcher
- synthesizer

没有更复杂的：
- 子任务分裂
- 并行 worker
- merge / arbitration
- long-horizon graph scheduler

### 6.3 learning 还是轻量实现

目前 memory 是：
- JSONL
- token overlap 检索

还没有：
- embeddings
- 向量数据库
- 任务级长期知识图谱
- 结构化经验回放

### 6.4 provider 层还没有流式输出

虽然模型可以正常调用，但 TUI 现在主要显示 trace，不是 token streaming chat。

### 6.5 权限系统是 agent-level，不是 OS-level

当前的权限控制是 DeerMes 自己的工具权限系统，不是容器隔离，也不是系统级沙箱。

## 7. 我对当前 DeerMes 架构的判断

如果用一句话总结：

当前 DeerMes 是一个“分层清晰、可运行、可持久化、可权限控制”的 agent scaffold，核心是 `runtime + execution + learning + tools + provider + security` 六层，外面包了 `CLI/TUI` 交互层，里面还区分了 `single-agent` 和 `deerflow` 两种执行架构。

它已经具备产品雏形，但还没有进入“成熟 agent platform”阶段。最成熟的是模块边界，最不成熟的是动态规划、长期学习深度和更完整的多代理编排。

## 8. 关键代码入口索引

- 入口：`src/deermes/cli.py`
- TUI：`src/deermes/tui.py`
- 单代理 runtime：`src/deermes/runtime/app.py`
- DeerFlow runtime：`src/deermes/runtime/deerflow_app.py`
- Agent loop：`src/deermes/runtime/loop.py`
- Planner：`src/deermes/execution/planner.py`
- DeerFlow supervisor：`src/deermes/execution/deerflow/supervisor.py`
- Learning：`src/deermes/learning/*`
- Tools：`src/deermes/tools/*`
- Providers：`src/deermes/providers/*`
- Security：`src/deermes/security.py`
- Permission config：`deermes.permissions.json`
