# 开发进度

> 每阶段记录：完成项、实际执行的验证命令与结果、遗留问题。
> 未执行的测试不得标注"通过"；依赖外部凭证未实测的功能一律标注"未实测"。

## Phase 3 — 最小审查闭环（2026-09-24 完成）

### 完成项

- `llm/openai_compat.py`：真实 LLM 网关（OpenAI 兼容 chat/completions，httpx 直连）。
  - 凭证只从环境变量读取（默认 `OPENAI_API_KEY`，可配置变量名），缺 key 启动即拒（`llm:no_api_key`），不写入任何文件/日志。
  - `base_url` 可指向任意兼容服务；`response_format: json_object` 可开关；temperature/max_tokens 可配置。
  - 错误映射：401/403→auth_failed，429→rate_limited，网络→network，响应结构异常→invalid_response（`LLMError`）。
- `llm/gateway.py`：`LLMError`（带 kind）；Mock 与 OpenAI 网关实现同一 `LLMGateway` 协议，`build_gateway` 按 `configs/agent.yaml` 的 `model.provider` 构造，CLI 支持 `--llm openai|mock` 覆盖。
- Finding 模型补齐题目字段：`trigger`（触发条件）进入 `LLMFinding` Schema、`Finding`、报告渲染与 Mock 规则。
- `examples/expected_findings.md`：演示 diff 的预期结果说明（7 条发现的完整矩阵），e2e 断言其中核心 5 条标题 + 触发条件渲染。
- Prompt 更新：优先级（缺陷与安全 > 边界与异常 > 有证据的性能）+ trigger 字段入 Schema 说明。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **88 passed**（新增：OpenAI 网关单元 9、OpenAI mock-server 全流水线集成 2、e2e 预期集合强化） |
| 集成：OpenAI 网关 + MockTransport 接入 graph | 2 个工作单元→2 次调用→usage 记账→finding.origin=call_id→报告生成，全链路通过 |
| 集成：模型返回非 JSON | 单元标记 failed、errors 记录、报告含"错误"章节、流水线不中断（故障隔离验证） |

真实模型验证状态：**未实测**（本机无 OPENAI_API_KEY）。OpenAI 网关逻辑经 MockTransport 全链路验证；
具备凭证时运行 `cra review examples/buggy.diff --llm openai` 即可单独验证真实调用。

### 遗留 / 下一步

- Phase 4：BaseReviewTool 完整接口（输入/输出 Schema、适用文件类型、超时与资源约束）、Tool Dispatcher、工具失败隔离与重试控制、typecheck 示例（默认 disabled）。

## Phase 2 — 三种输入与 Diff 归一化（2026-09-24 完成）

### 完成项

- `providers/github.py`：GitHub PR Adapter——URL 解析、元数据（title/body/base sha/head sha）、`Accept: application/vnd.github.diff` 拉 raw diff、`GITHUB_TOKEN`（Bearer，仅随请求发送，不落日志）、diff 大小上限、空 diff 拒绝。
- `providers/gitlab.py`：GitLab MR Adapter——支持嵌套 group（project path URL-encode）、`diff_refs` 基准/目标 SHA、`PRIVATE-TOKEN` 鉴权、changes 接口按文件片段重建标准 unified diff（new_file/deleted_file 标记转 `--- /dev/null` 等）。
- `providers/base.py`：`ProviderError`，状态码 `not_found / auth_failed / rate_limited / too_large / network / invalid_input / http`，CLI 将其写入任务记录（task.error 含 `[kind]`）并以退出码 1 终止。
- `providers/__init__.py`：`detect_source`（github/gitlab/local URL 识别，含自建 GitLab 域名）+ `build_provider` 工厂；CLI 接入，`-` 支持 stdin。
- `diff/parser.py`：重命名支持（`rename from/to` → `kind=renamed`，保留 hunk 可审查）。
- `agent/work_units.py`：超大 hunk 按字节上限切分为多个 chunk（unit_id `#h0c0/#h1c1…`），每个 chunk 保留精确新文件行号；纯删除行 chunk 不产出；Mock 扫描 chunk 行号正确性有测试。
- CLI：三种输入统一入口；ProviderError 带明确状态落库。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **77 passed**（新增：GitHub adapter 10、GitLab adapter 8、provider 选择/local 7、work_units 分块 6、validator 7、GitHub mock 全流水线集成 1、CLI ProviderError 1） |
| `.venv/Scripts/python -m code_review_agent.cli review examples/buggy.diff` | 回归通过 |
| 集成测试：MockTransport 注入 httpx.Client | PR/MR 全流水线（provider→graph→报告）跑通，SHA 进入报告"变更区间"，secret 不出现在报告中 |

平台集成状态声明：GitHub/GitLab 适配器**仅通过 httpx MockTransport 固定响应验证**，未用真实令牌对真实平台发起请求（无凭证，按 plan 标注为**未实测**）。

### 值得记录的发现

- 测试 fixture 曾把 4 行新增误标为 `+1,3`，validator 按设计把越界行号的 finding 丢弃——反向验证了"不把无法定位的评论强行映射到代码行"这一验收点。
- httpx MockTransport 注入的 client 无 base_url，相对路径会失败；provider 改为拼绝对 URL，且鉴权头随每个请求发送（注入 client 也可测鉴权）。

### 遗留 / 下一步

- Phase 3：真实 LLM 网关（OpenAI-compatible），统一 Mock/真实调用接口；预期结果演示 diff。
- GitLab `changes` 接口在极新版本 GitLab 中被分页 `diffs` 接口替代；真实集成时如失败将切换（已标注未实测）。

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
