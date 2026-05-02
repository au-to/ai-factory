# AI Factory

AI Factory 是一个**流水线编排层**，将 Claude Code CLI 作为 AI 运行时，驱动软件开发的完整生命周期 —— 从需求分析到部署上线。

**核心理念**: 集成而非重建。SDLC 每个阶段已有成熟工具，AI Factory 提供的是连接它们的**工作流引擎**、**上下文总线**和**人工审批检查点**。

---

## 流水线概览

```
需求分析 → [审批: PRD] → 产品设计 → [审批: 技术方案]
    → 开发 → 测试 → [审批: 测试报告] → 部署 → [审批: 部署]
```

五个阶段，四个审批门。每个阶段由独立的 AI Agent 执行，上游产出物自动传递到下游。审批门暂停流水线，等待人工审核后方可继续。

完整的流水线（以生成一个 React + Express TODO 应用为例）耗时约 80 分钟，其中 AI 工作 ~80 分钟，人工审批累计只需数分钟。

---

## 系统架构

AI Factory 由四个子系统构成：

### 1. Pipeline Engine（流水线引擎）

**职责**: 有限状态机，驱动阶段转换，处理重试，协调审批门。

```
pending → in_progress → awaiting_approval | completed | failed
                              ↕
                     approved / rejected / changes_requested
```

核心代码: `factory/pipeline/engine.py:21` — `PipelineEngine`

- `PipelineEngine.run()` 按配置顺序执行阶段，每个阶段失败后自动重试（默认 3 次）
- `PipelineEngine._await_gate()` 每 5 秒轮询一次 DECISION 文件，最长等待 8 小时
- 状态持久化在 SQLite (`factory/pipeline/state.py`) 中，dashboard 重启不影响已启动的流水线线程

`StageRunner` (`factory/pipeline/runner.py`) 负责单阶段执行:

1. 从 `PromptBuilder` 获取渲染后的提示词（含上游阶段产物作为上下文）
2. 调用 `ClaudeRunner` 执行 Claude Code CLI
3. 从 stdout 或 Json 文件中解析结构化产物
4. 验证并通过 `ContextBus` 存储

### 2. Context Bus（上下文总线）

**职责**: 版本化、目录式产物存储。所有阶段输出同时生成结构化 JSON 和人类可读的 Markdown。

存储布局:

```
workspace/
  artifacts/
    <run-id>/
      manifest.json              # 版本索引 {"requirements":{"requirements.json":1}}
      requirements/
        v1/requirements.json     # PRD 结构化数据
        v1/requirements.md       # PRD 人类可读 Markdown
      design/
        v1/design.json           # 技术方案
      development/
        v1/development.json      # 构建日志（所有生成文件清单）
      testing/
        v1/testing.json          # 测试报告
      deployment/
        v1/deployment.json       # 部署配置
```

核心代码: `factory/bus/store.py:13` — `ContextBus`

- `write()` 自动递增版本号，同时写 JSON + MD
- `read()` 按阶段名和 run_id 读取最新版本产物
- `write_raw()` 接收未验证的 dict，注入 `run_id`/`stage`/`version` 后通过 Pydantic 验证

产物 Schema 定义在 `factory/bus/schemas.py`:

| 阶段 | Schema 类 | 关键字段 |
|------|-----------|----------|
| requirements | `PRDArtifact` | title, problem_statement, functional_requirements, assumptions |
| design | `TechSpecArtifact` | overview, components, data_models, api_contracts, tech_stack |
| development | `BuildLogArtifact` | summary, files_created, components_completed, issues_encountered |
| testing | `QaReportArtifact` | total_tests, passed, failed, coverage_percent, test_results |
| deployment | `DeployConfigArtifact` | services, dockerfile_path, docker_compose_path |

**设计原则**: 一切皆文件。用 `cat` 和 `git diff` 即可调试，无黑箱。

### 3. Agent System（智能体系统）

**职责**: 每个流水线阶段的 AI Agent 配置和提示词渲染。

Agent 配置 (YAML) 包含:

```yaml
name: "Developer"                # Agent 标识
model: "claude-sonnet-4-20250514"
temperature: 0.2                  # 代码类任务较低温
system_prompt: |                  # 行为约束和输出格式
  You are an expert Full-Stack Developer...
allowed_tools: [...]              # 可用工具白名单
output_schema: "schemas/build-log-artifact.json"  # 产物 JSON Schema
```

5 个预置 Agent:

| Agent | 用途 | 允许工具 | 温度 |
|-------|------|----------|------|
| Requirements Analyst | 需求分析 → PRD | Read, Write, Glob, Grep | 0.3 |
| System Architect | 系统设计 → 技术方案 | Read, Write, Glob, Grep | 0.3 |
| Developer | 代码生成 | Read, Write, Glob, Grep, Bash(npm/git/mkdir) | 0.2 |
| QA Engineer | 测试生成与执行 | Read, Write, Glob, Grep, Bash(npm/npx) | 0.2 |
| DevOps Engineer | 容器化部署 | Read, Write, Glob, Grep, Bash(docker) | 0.2 |

提示词使用 Jinja2 模板渲染，上游产物自动注入上下文:

```jinja2
{# templates/prompts/development.j2 #}
{% if design %}
## Technical Specification
The tech spec is available at: {{ design_path }}
{% endif %}
{% if requirements %}
## Requirements
The PRD is available at: {{ requirements_path }}
{% endif %}
{% if feedback %}
## Feedback from Previous Attempt
{{ feedback }}
{% endif %}
```

核心代码:
- `factory/agents/registry.py` — Agent 配置加载
- `factory/agents/prompt.py` — Jinja2 提示词渲染，自动读取上游产物注入上下文
- `factory/tools/claude.py` — Claude Code CLI 子进程包装器

### 4. Human Gates（人工审批门）

**职责**: 在关键检查点暂停流水线，等待人工审批。

Gate 生命周期:

```
1. 阶段完成 → 创建 GATE_REVIEW.md（含产物摘要和审批指引）
2. 人工通过 CLI / Web / 文件编辑做出决定
3. 流水线每 5 秒轮询 DECISION 文件
4. approved → 继续下一个阶段
   rejected → 终止流水线
   changes_requested → 读取 FEEDBACK.md，带反馈重试当前阶段
```

Gate 文件结构:

```
workspace/gates/<run-id>/<stage>/
  GATE_REVIEW.md    # 产物摘要 + 审批指引
  DECISION          # 内容为 approved | rejected | changes_requested
  FEEDBACK.md       # 请求修改时的反馈（可选）
```

三种审批方式:
- **CLI**: `factory approve/reject/changes <run-id> <stage>`
- **Web**: Dashboard run 详情页点击 Approve / Reject / Request Changes
- **直接写文件**: `echo 'approved' > workspace/gates/<run-id>/<stage>/DECISION`

核心代码: `factory/gates/gate.py` — `GateManager`

---

## 项目结构

```
ai-factory/
├── factory/                    # Python 编排层
│   ├── pipeline/               # 流水线引擎
│   │   ├── engine.py           #   PipelineEngine — 核心状态机
│   │   ├── runner.py           #   StageRunner — 单阶段执行器
│   │   ├── config.py           #   StageConfig / PipelineConfig Pydantic 模型
│   │   └── state.py            #   PipelineState — SQLite 状态持久化
│   ├── bus/                    # 上下文总线（产物存储）
│   │   ├── store.py            #   ContextBus — 版本化读写
│   │   ├── schemas.py          #   5 个 Pydantic 产物 Schema
│   │   └── manifest.py         #   Manifest — 版本索引管理
│   ├── gates/                  # 人工审批门
│   │   ├── gate.py             #   GateManager — 文件轮询审批
│   │   └── review.py           #   GATE_REVIEW.md 模板渲染
│   ├── agents/                 # Agent 配置
│   │   ├── registry.py         #   AgentRegistry — YAML → AgentConfig
│   │   └── prompt.py           #   PromptBuilder — Jinja2 提示词渲染
│   ├── tools/                  # AI 运行时
│   │   └── claude.py           #   ClaudeRunner — Claude Code CLI 子进程
│   ├── web/                    # Web 面板
│   │   ├── app.py              #   FastAPI + PreviewManager + 路由
│   │   └── templates/          #   Jinja2 前端模板
│   │       ├── base.html       #     基础布局 + 全部 CSS
│   │       ├── index.html      #     Run 列表页
│   │       ├── run.html        #     Run 详情页（流水线可视化 + 审批 + 预览）
│   │       └── artifact.html   #     产物详情页
│   ├── cli.py                  # CLI 入口（7 个子命令）
│   └── __main__.py             # python -m factory 入口
├── configs/                    # YAML 配置
│   ├── pipelines/default.yaml  #   5 阶段流水线定义
│   └── agents/                 #   5 个 Agent 行为配置
├── templates/prompts/          # Jinja2 提示词模板（每个阶段一个）
├── schemas/                    # JSON Schema（产物格式参考）
├── tests/                      # 单元测试
├── workspace/                  # 运行时产物（gitignored）
│   ├── artifacts/              #   流水线产出物
│   ├── gates/                  #   审批文件
│   ├── project/                #   AI 生成的代码项目
│   └── sessions/               #   Claude stdout 日志
└── pyproject.toml              # Python 项目配置
```

---

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| 编排层 | Python 3.12+ | 流水线引擎、状态管理、Web 面板 |
| 数据模型 | Pydantic v2 | 5 个产物 Schema，配置验证 |
| 配置格式 | YAML | 流水线定义 + Agent 配置 |
| 提示词模板 | Jinja2 | 上游产物自动注入上下文 |
| 状态持久化 | SQLite | 3 张表：runs, stages, gates |
| Web 框架 | FastAPI + Uvicorn | 路由、后台线程执行流水线 |
| AI 运行时 | Claude Code CLI | 子进程调用，`-p` print 模式，`--permission-mode auto` |
| 前端 | Jinja2 模板 + 内联 CSS/JS | 无构建步骤，零 JS 依赖 |

---

## 快速开始

### 环境要求

- Python 3.12+
- Claude Code CLI (`claude` 命令可用)
- Node.js 18+（生成的代码项目需要）
- Docker（部署阶段需要）

### 安装与运行

```bash
# 1. 安装
git clone <repo-url> && cd ai-factory
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. 验证 Claude Code CLI
claude --version

# 3. 运行流水线
factory run --prompt "Build a TODO app with React frontend and Express backend"

# 4. 在另一个终端查看状态
factory status

# 5. 审批门户（流水线暂停时）
factory approve <run-id> requirements
```

### 使用 Web 面板

```bash
# 启动面板
factory dashboard
# 打开 http://127.0.0.1:8900

# 在 Web UI 中：
#  - 首页输入 prompt 启动新流水线
#  - 详情页查看流水线可视化 + 审批按钮
#  - deployment 完成后"启动预览"查看运行中的应用
```

### 产出的 Todo 应用预览

流水线完成后，在 run 详情页可看到「应用预览」卡片：

- 点击「启动预览」→ 自动执行 `npm run dev` → 前端 :3000，后端 :3001
- 绿色脉冲指示灯表示正在运行
- 点击链接直接打开应用
- 点击「停止预览」清理进程

预览功能由 `PreviewManager` (`factory/web/app.py:75`) 管理进程生命周期。

---

## CLI 命令参考

```bash
# 运行流水线
factory run --prompt "你的项目描述"        # 自动生成 run_id
factory run --prompt "..." --run-id myapp  # 指定 run_id
factory run --prompt "..." --pipeline default  # 指定流水线配置

# 审批操作
factory approve <run-id> <stage>           # 批准
factory reject <run-id> <stage> --reason "不符合预期"  # 拒绝
factory changes <run-id> <stage> --feedback "请增加错误处理"  # 请求修改

# 查询
factory status                              # 查看最近一次运行
factory status <run-id>                     # 查看特定运行
factory list                                # 最近 20 次运行
factory list --limit 50                     # 最近 50 次运行

# Web 面板
factory dashboard                           # 默认 127.0.0.1:8900
factory dashboard --port 9000 --host 0.0.0.0
```

---

## 数据流详解

一次完整的流水线运行：

```
用户输入 prompt
    │
    ▼
PipelineEngine.run()
    ├─ 创建 run 记录 (SQLite runs 表)
    │
    ├─ Stage 1: requirements
    │   ├─ PromptBuilder 渲染 requirements.j2（注入 user_prompt）
    │   ├─ ClaudeRunner 调用 claude -p "..." --system-prompt "..."
    │   ├─ StageRunner 解析 stdout JSON → PRDArtifact
    │   ├─ ContextBus.write() → artifacts/<run>/requirements/v1/requirements.json + .md
    │   ├─ GateManager.create_gate() → gates/<run>/requirements/GATE_REVIEW.md
    │   └─ GateManager.wait_for_decision() → 每 5s 轮询 DECISION 文件
    │
    ├─ Stage 2: design
    │   ├─ PromptBuilder 渲染 design.j2（注入 requirements 产物内容 + 路径）
    │   ├─ ClaudeRunner 调用 → TechSpecArtifact
    │   └─ Gate: Technical Design Approval
    │
    ├─ Stage 3: development
    │   ├─ PromptBuilder 注入 design + requirements
    │   ├─ working_dir = workspace/project/（代码在这生成）
    │   ├─ ClaudeRunner 调用（允许 Bash npm/git 等工具）
    │   └─ 无 gate，直接继续（代码生成不需要审批）
    │
    ├─ Stage 4: testing
    │   ├─ PromptBuilder 注入 development + design + requirements
    │   ├─ ClaudeRunner 调用（允许 npm/npx/pytest）
    │   └─ Gate: Test Results Approval
    │
    ├─ Stage 5: deployment
    │   ├─ PromptBuilder 注入 development + design
    │   ├─ ClaudeRunner 调用（允许 Docker 工具）
    │   └─ Gate: Deployment Approval
    │
    └─ PipelineEngine: 更新状态为 completed
```

产物传递规则：`StageConfig.input` 字段定义了每个阶段能读取哪些上游产物。例如 design 阶段 `input: ["requirements"]` 意味着渲染提示词时自动注入 requirements 产物。

---

## 配置系统

### 流水线配置 (`configs/pipelines/default.yaml`)

```yaml
stages:
  - name: development
    agent: "Developer"              # 使用的 Agent 名称
    input: ["design", "requirements"]  # 上游阶段（注入产物上下文）
    output_artifact: development    # 产物阶段名
    gate: false                     # 是否暂停等待审批
    max_turns: 50                   # Claude 最大对话轮次
    allowed_tools: [...]            # 工具白名单（覆盖 Agent 默认）
    working_dir: "project"          # 相对 workspace 的子目录
    retry_limit: 2                  # 失败重试次数
```

### Agent 配置 (`configs/agents/*.yaml`)

每个 Agent 定义模型、温度、行为约束和输出格式。新增 Agent 只需在 `configs/agents/` 添加 YAML 文件。

### 提示词模板 (`templates/prompts/*.j2`)

Jinja2 模板，渲染时可访问所有上游产物变量和 `extra_context`。例如 development.j2 可访问 `{{ requirements }}`、`{{ design }}`、`{{ feedback }}`。

---

## 扩展指南

### 添加新阶段

1. 在 `configs/pipelines/default.yaml` 的 `stages` 列表中添加阶段定义
2. 在 `factory/bus/schemas.py` 中添加对应的 Schema 类和 `STAGE_ARTIFACT_MAP` 映射
3. 创建 Agent 配置 `configs/agents/new-agent.yaml`
4. 创建提示词模板 `templates/prompts/new-stage.j2`
5. （可选）在 `factory/web/app.py` 的 `STAGE_ORDER` 和 `STAGE_LABELS` 中注册

### 添加新 Agent

在 `configs/agents/` 下创建 YAML 文件即可自动加载:

```yaml
name: "Security Auditor"
description: "Security review of generated code"
model: "claude-sonnet-4-20250514"
temperature: 0.1
system_prompt: |
  You are a security auditor. Review the codebase for vulnerabilities...
allowed_tools: ["Read", "Glob", "Grep", "Bash(npm audit *)"]
```

---

## 开发调试

```bash
# 运行单元测试
python3 -m pytest tests/ -v

# 查看产物
cat workspace/artifacts/<run-id>/requirements/v1/requirements.json | python3 -m json.tool

# 查看 Claude stdout 日志
cat workspace/sessions/<run-id>/<stage>-output.txt

# 查看审批文件
cat workspace/gates/<run-id>/<stage>/GATE_REVIEW.md

# 直接操作审批（绕过 CLI）
echo 'approved' > workspace/gates/<run-id>/<stage>/DECISION
echo 'rejected' > workspace/gates/<run-id>/<stage>/DECISION

# 重新初始化（清空所有运行数据）
rm -rf workspace/artifacts workspace/gates workspace/sessions workspace/sessions
```

### 常见问题

- **JSON 解析失败**: Claude 有时在 JSON 字符串中包含未转义的换行符 → `_sanitize_json_strings()` 自动修复
- **JSON 不在 stdout 中**: Claude 通过 Write 工具直接写文件 → `_read_claude_json_files()` 回退读取
- **npm install 挂起**: 检查 npm registry 配置 (`npm config get registry`)，默认应为 `https://registry.npmjs.org/`
- **流水线线程在 dashboard 重启后丢失**: 后台线程随进程结束。长时间运行请用 `factory run` CLI 模式（独立进程），Web 面板仅用于审批

---

## 设计决策

1. **一切皆文件**: 产物 (JSON+MD)、审批 (DECISION)、日志 (sessions/*.txt) 全部明文落盘，无需专用调试工具
2. **Claude Code CLI 作为唯一 AI 运行时**: 不调用 API，不依赖 SDK。`claude -p` print 模式一次性执行，stdout 捕获 + 文件回退双重提取
3. **人工审批 = 文件轮询**: 不需消息队列、不需 WebSocket。流水线每 5 秒检查一次 DECISION 文件，CLI/Web/直接编辑三种方式等价
4. **Pydantic 宽松验证**: 部分字段使用 `dict` 而非严格类型（如 `tech_stack`），容忍 AI 输出的合理变化
5. **无 JS 依赖的前端**: 全部用 Jinja2 + 内联 CSS + 少量 fetch JS，无需 npm 构建步骤
