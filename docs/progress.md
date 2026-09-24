# 开发进度

> 每阶段记录：完成项、实际执行的验证命令与结果、遗留问题。
> 未执行的测试不得标注"通过"；依赖外部凭证未实测的功能一律标注"未实测"。

## Phase 1 — 初始化与工程骨架（2026-09-24 完成）

### 完成项

- 工程骨架：`pyproject.toml`（hatchling + src 布局，CLI 入口 `cra`/`python -m code_review_agent.cli`）、`.gitignore`、`.env.example`、`configs/{agent,tools,model_pricing}.yaml`、`scripts/{run_tests,demo}.sh`。
- 结构化模型与 Schema 验证（pydantic v2，`extra="forbid"`）：
  - `agent/state.py` — LangGraph `GraphState`（TypedDict，errors/node_trace 用 `operator.add` 合并）
  - `review/finding.py` — `Finding`（severity/confidence 枚举、行号 `ge=1`）
  - `llm/schemas.py` — `LLMFinding`/`LLMFindingOutput`（LLM 输出强校验，支持 ```json 围栏剥离）
  - `agent/work_units.py` — `WorkUnit`（pending/running/done/failed/skipped 状态机，sha256 指纹）
  - `tools/base.py` — `ToolResult` 统一工具结果
- LangGraph 工作流（`agent/graph.py` + `agent/nodes.py`）：固定控制流 10 节点
  `load_input → security_scan → normalize_diff → build_context → select_tools → execute_tools → llm_analyze → validate_findings → generate_report → publish`。
  与 plan.md 控制流的差异：security_scan 前置于 normalize_diff，原因是先脱敏再解析可保证行号零漂移（脱敏只做同行内替换，不增删行）。
- 抽象层：`llm/gateway.py`（LLMGateway 协议 + Mock 实现）、`providers/base.py`（RepositoryProvider 协议 + LocalDiffProvider）、`publishers/base.py`（ReviewPublisher 协议 + DryRunPublisher）。主流程零平台/模型 SDK 依赖。
- SQLite 持久化（SQLAlchemy 2.0）：`persistence/models.py` 的 `tasks`/`findings` 表；CLI 运行即落库。
- 观测性雏形：`observability/logging.py` JSON-lines 结构化日志（每次任务 `runs/<task_id>/log.jsonl`）。
- 声明式工具注册雏形：`tools/registry.py`（`@install` + `configs/tools.yaml` enabled 开关）；首个工具 `diff-stat`（纯文本统计，不执行仓库代码）。新增工具 = 新实现文件 + yaml 登记，主流程零改动（已有测试证明）。
- Mock 路径：`llm/mock.py` 确定性规则模拟器（非真实模型，已在类 docstring 与报告中标注），无网络、无 API Key 可跑通 START→END。
- CLI：`cra review <diff 路径>`，默认 dry-run；任务失败会落库 status=failed 并以退出码 1 结束。

### 六项能力在 Phase 1 的落地程度（诚实声明）

| 能力 | 状态 |
|---|---|
| 可恢复 | 骨架：任务/发现已落 SQLite，WorkUnit 状态机就绪；LangGraph checkpointer 与 `resume` 命令在 Phase 5 |
| 可观测 | 骨架：JSON 日志 + finding.origin=LLM call_id；comment→trace 全链路查询在 Phase 6 |
| 可扩展 | 雏形可用：注册表 + yaml 开关（Phase 4 补全输入/输出 Schema、超时、文件类型过滤、执行沙箱） |
| Token 预算 | 仅记录用量统计，预算闸门未实现（Phase 7，报告中已明确标注） |
| 置信度 | 最小版：行号范围校验 + 高置信必须"行可定位且证据非空"（Phase 8 补证据核查与复核） |
| 安全 | 最小版：进 LLM 前三类 secret 正则脱敏（fail-closed 思路）；Phase 9 补全覆盖面、注入防护对抗测试、执行沙箱 |

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `uv pip install -e ".[dev]"`（Python 3.11.7） | 安装成功（langgraph/pydantic 2.13.5/sqlalchemy 2.0.54/typer 0.27.2） |
| `.venv/Scripts/python -m pytest tests/` | **38 passed**（unit 22 + tools 5 + e2e CLI 3 + fixtures 计入） |
| `.venv/Scripts/python -m code_review_agent.cli review examples/buggy.diff` | 完成：7 findings（高置信 3 / 参考 4），报告 `runs/<id>/report.md` |
| `.venv/Scripts/python -m code_review_agent.cli review examples/clean.diff` | 完成：0 findings，报告含"未发现可确认问题" |
| `bash scripts/run_tests.sh` / `bash scripts/demo.sh` | 均通过 |
| SQLite 落库检查 | tasks=completed ×2，findings 3 high + 4 reference，与报告一致 |

安全验证（e2e 断言覆盖）：`sk-live-9f8e7d6c5b4a3210` 不出现在报告/redacted diff 中，报告中该行为 `[REDACTED:generic-secret]`；Mock 捕获的 findings 证据亦为脱敏后文本。

### 修复记录

- typer 单命令折叠导致 `review` 被当作位置参数 → 增加 `@app.callback()` 强制子命令模式。
- `@install` 注册的是类而非实例，`diff-stat` 调用丢 `self` → `install` 支持类/实例并实例化；e2e 增加 `diff-stat: success` 断言防回归。
- Mock 的 `except: pass` 规则原为单行匹配，示例中跨两行未命中 → 增加跨行检测。
- conftest `REPO_ROOT` 层级错误（`parents[2]`→`parents[1]`）。
- `diff-stat` 统计预期 29→25（19+6 新增行）。

### 遗留 / 下一步

- Phase 2：GitHub PR / GitLab MR Adapter（含 Mock API 测试）、重命名/二进制细化、过大 diff 明确状态、上下文分块与行号映射强化。
- typer help 在 GBK 控制台下乱码：`main()` 已将 stdout/stderr 重配为 UTF-8，Git Bash/Windows Terminal 下正常；老式 cmd 可能仍需 `chcp 65001`。
