# 开发进度

> 每阶段记录：完成项、实际执行的验证命令与结果、遗留问题。
> 未执行的测试不得标注"通过"；依赖外部凭证未实测的功能一律标注"未实测"。

## Phase 10 — GitHub/GitLab 发布（2026-09-24 完成）

### 完成项

- `publishers/github.py` `GitHubReviewPublisher`：PR 行级评论（`path/line/side=RIGHT/commit_id`），
  汇总 Markdown 始终保留本地。
- `publishers/gitlab.py` `GitLabMRPublisher`：MR discussions + position（`new_path/new_line/base/start/head_sha`）。
- **发布前校验**：拉取平台元数据比对 head SHA（任务基于的 diff 与当前不一致 → 拒绝发布，
  防评论错位）；只发布能精确定位到**新增行**的 findings，无法定位的仅保留在报告（receipt 说明数量）。
- **幂等去重（两层）**：①评论 body 内嵌稳定锚点 `<!-- CRA-FINDING:{id} -->`，发布前先拉取远端
  既有评论，已存在锚点跳过——覆盖"远端已成功、本地记账中断"的恢复场景；②本地 `publications`
  表 sent 记录直接复用 receipt（Phase 5 机制，failed 记录允许重试）。
- **显式授权**：`--publish`（review 与 resume 均可）；默认 dry-run；本地 diff 无发布目标 → 明确报错。
  平台令牌只从 `GITHUB_TOKEN` / `GITLAB_TOKEN` 环境变量读取。
- 发布评论 body 渲染前再过 redact（外发面纵深防御，有测试）。
- 发布失败（429/401/网络）→ receipt.failed + 落库 failed + 日志告警，可 resume 重试。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **151 passed**（新增 publishers 9 + CLI 发布门控 2） |
| Mock API 发布测试 | 行级参数（path/line/side/commit_id 与 position 字段）断言；锚点断言；重复发布 0 新增；SHA 不一致拒绝（0 条发出）；无法定位仅留报告；429→failed receipt；评论 body 无 secret |
| CLI e2e | 默认 dry-run；`--publish` 走发布器并写 sent 记录；resume 复用 sent 记录不再调用发布器（幂等）；本地 diff + `--publish` → exit 1 |

真实平台发布状态：**未实测**（无测试仓库与授权凭证）。发布器逻辑全部经 httpx MockTransport 验证；
具备凭证时对测试仓库运行 `cra review https://github.com/<测试组织>/<测试仓库>/pull/1 --publish` 即可单独验证。

### 下一步

- Phase 11：最终测试、文档与打包——全量回归、一键演示脚本、架构/安全文档、示例工件、干净环境复现。

## Phase 9 — 安全边界（2026-09-24 完成）

### 完成项

- **fail-closed 输入检查**：`security.assert_safe_text`——NUL 字节或不可打印控制字符 → 拒绝处理；
  LocalDiffProvider 改严格 UTF-8 解码（解码失败即 ProviderError）。
- **脱敏全覆盖**（此前 diff 已覆盖，补齐其余出口）：
  - 工具输出：execute_tools 持久化前对序列化 output/error 过 redact；
  - 模型回显：finding.evidence（来自模型响应）入库前再脱敏；
  - 外发 trace：导出 JSON 二次脱敏（Phase 6 已有）；日志 extra 仅安全标量（约束在代码）；
  - PR/MR 描述：设计上不进入 prompt（ReviewInput.description 仅供人读）。
- **沙箱执行**（`tools/sandbox.py`）：DockerSandboxRunner——`--network none`、`--read-only`、
  `--cap-drop ALL`、`no-new-privileges`、内存/CPU/PID 上限、临时目录只读挂载（文件名取 basename 防穿越）、
  `--stop-timeout` 进程级强杀、不挂载任何宿主凭证；docker 不可用 → `available=False` →
  执行型工具自动禁用（fail-closed to disabled）。FakeSandboxRunner 供确定性测试。
- **typecheck 真实沙箱执行**（unit 作用域）：沙箱可用时对全量新增文件跑容器内纯 `compile()`
  （不 import、不执行被审代码逻辑）；命令固定、无 shell 拼接，diff/模型内容无法注入命令。
  沙箱不可用时 Dispatcher 入口 + run() 双重拒绝。
- **Prompt Injection 边界**：控制流层面，工具选择/沙箱开关/脱敏全部由确定性代码决策，
  diff 与模型输出只是数据；prompt 中显式声明 `<diff>` 为被审数据。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **140 passed**（新增 security/adversarial 9） |
| 对抗测试 | ①注入指令 diff（要求启用 typecheck/执行 rm -rf/跳过脱敏）→ 工具集不变、脱敏照常、findings 仍只来自规则检出；②secret 不进模型请求（捕获网关断言 prompt 无原值、含掩码）；③NUL 字节 fail-closed；④非法 UTF-8 拒绝；⑤typecheck 仅经沙箱执行（fake 断言命令固定为 compile）；⑥沙箱不可用保持禁用；⑦无 docker 时 DockerSandboxRunner.available=False；⑧GITHUB_TOKEN 不出现在报告/日志/LLM 调用快照 |
| 本机环境 | docker 不存在 → 沙箱自动禁用（日志告警），行为与验收一致：**隔离无法验证则保持禁用** |

### 已知限制

- Docker 沙箱逻辑经 FakeSandboxRunner 验证 + docker 命令拼装有单测；**未在真实 docker 环境实测**（本机无 docker）。
- Secret 检测为正则模式集（AWS key/私钥/带引号与裸值 generic-secret），非 gitleaks 全量规则库；
  高熵检测等留作后续增强。fail-closed 场景覆盖编码与控制字符，非穷尽。

### 下一步

- Phase 10：GitHub/GitLab 发布（行级评论、`--publish` 显式授权、幂等去重、SHA/位置校验）。

## Phase 8 — Finding 验证与置信度分级（2026-09-24 完成）

### 完成项

- **确定性验证规则**（`review/validator.py` 重写，全部理由写入 finding 字段，报告与 trace 可查）：
  - 文件在 diff 内、行号在 hunk 新行范围内（既有）；
  - **证据核验**：evidence 文本（归一化后）必须确实出现在该文件的 diff 内容中，否则降级；
  - **变更相关性**：高置信度的行号必须指向**新增行**（`+` 行）——指向 context 行（未变更代码）降级为参考；
  - **工具交叉佐证**：工具报告的错误位置（如 py-ast-check 语法错误行）与 finding 位置重合 →
    `tool_evidence` 记录工具名，且可据此升级为高置信（独立静态证据）；
  - **相似合并**：同文件、行距 ≤3、标题相似度（SequenceMatcher）≥0.8 → 合并去重（精确重复继续丢弃）。
- **预算内有上限的复核**（`review/recheck.py` `RecheckService`）：
  - 只复核高置信候选，上限 `review.recheck_max`（默认 5，服务自身强制）；
  - 复核调用走同一预算闸门（不足则跳过复核、不阻塞主流程）；
  - verdict：confirmed → 保持高置信并记录复核信息；rejected → 移除（dropped 说明原因）；
    uncertain → 降级为参考（理由入 downgrade_reason）；
  - 复核调用同样入 llm_calls（prompt/响应脱敏快照）与 span（`recheck:<finding_id>`，父=validate_findings 节点）；
  - 复核结果与分级结果在 validate_findings 节点落库（幂等 upsert），直跑 graph 与 CLI 行为一致。
- LLM 自报 confidence 仅为输入信号；最终分级 = 确定性规则 + 工具佐证 + 复核裁决。
- Mock 网关支持 `purpose=recheck` 请求：按规则扫描证据给 confirmed/uncertain（确定性、可测）。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **131 passed**（新增：validator 7 规则测试 + recheck 8（confirmed/uncertain 降级/rejected 移除/上限/解析失败/全流水线复核入报告与 DB）） |
| e2e 断言强化 | 报告 finding 数 == DB 行数 == 7（曾发现 state 过滤 bug：参考级 findings 被误删——由 CLI 演示发现，e2e 补报告计数断言防回归） |
| trace | finding 链含 recheck（verdict/reason/call_id）、tool_evidence、downgrade_reason |
| CLI 演示 | `review examples/buggy.diff`：7 条（高置信 3 全部复核 confirmed；参考 4）；报告含复核行 |

### 修复记录

- validate_findings 的复核结果合并曾把非候选 findings 一并过滤掉（报告 3 条 vs 应为 7 条）——
  DB 行数与报告不一致被 CLI 演示暴露；改为 `rejected_ids` 精确剔除 + 候选映射替换。

### 下一步

- Phase 9：安全边界——脱敏全覆盖（工具输出/trace）、Prompt Injection 对抗测试、工具白名单强化。

## Phase 7 — Token/金额预算（2026-09-24 完成）

### 完成项

- `budget/pricing.py`：单价表（`configs/model_pricing.yaml`）——**未登记模型 fail-closed**（拒绝调用，
  防止不可控成本）；币种可配置。
- `budget/ledger.py`：预算账本——journal 逐条记账（reserve/settle/release，含 call_id 归属）
  + 汇总行（spent/reserved/calls）；**原子预留**（进程内 threading.Lock + SQLite 单写者）。
- `budget/controller.py`：调用前预留闸门：
  - 估算口径：输入 token ≈ prompt 字符数 / 4，成本 = 输入估算 + max_output_tokens 按单价折算；
  - `try_reserve` 不足 → 返回 None，调用方**不得发起付费调用**（该决策为确定性代码，LLM 无权绕过）；
  - 调用成功 → 按服务商返回的实际 usage **结算**；
  - usage 不可得（全 0）→ **保守保留预留额**（snapshot 中呈"待结算"），不虚减不虚增；
  - 调用失败 → release 释放预留。
- `agent/nodes.py` llm_analyze 集成：每个单元调用前预留；预算不足 → 当前与其后单元标记
  `skipped(budget)`，**停止后续付费调用**，流水线继续产出部分报告；
  - 已完成单元的结果与 findings 保留（部分结果语义）；
  - `resume` 会重试 failed 与 budget-skipped 单元（调高 `--budget` 后续审）。
- 报告预算行：上限/已结算/预留（待结算）/剩余 + "本地估算口径，非服务商精确账单"声明 + 未审查范围提示。
- CLI：`--budget <元>` 覆盖（review 与 resume 均可）。
- 持久化：`budget_entries`（journal）与 `budget_summary`（汇总）两张表，重启可续账。

### 分块与优先级调度说明

大 diff 分块在 Phase 2 已实现（hunk→chunk，字节上限）；本阶段的"优先级"采用 FIFO
（工作单元顺序即优先级），预算耗尽时按序截断——简单、可预测、与 resume 语义一致。
基于成本/收益的调度重排留作后续扩展（不影响闸门正确性）。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **118 passed**（新增预算 13：单价表 fail-closed、账本生命周期、限额拒绝、**20 线程并发预留不超额**、控制器估算/结算/无 usage 保守保留、流水线部分报告、预算 0 时网关 0 次调用） |
| 部分报告场景（单价 100 元/1k、预算 30 元） | 单元 1 完成结算（findings 保留），单元 2 预留被拒 → skipped；报告含预算行与"未审查"提示；`remaining = limit - spent - reserved` 恒等 |
| CLI 回归 | `review examples/buggy.diff` 报告新增预算行（mock 单价 0） |

### 已知限制

- 并发原子性覆盖进程内（threading.Lock）；跨进程并发预留依赖 SQLite 文件锁，未做分布式账本。
- 估算口径（字符/4）粗于 tokenizer 精确计数；预留偏保守方向（max_output 全额计入）。

### 下一步

- Phase 8：Finding 验证与置信度分级——工具证据交叉验证、重复合并、复核（预算内）、分级理由可查。

## Phase 6 — 评论级 Trace 与可观测性（2026-09-24 完成）

### 完成项

- **span 体系**（`observability/spans.py`）：`SpanRecorder` + contextvar 父子传递。
  - 节点 span（`node:*`，graph.py 统一包装，含异常捕获与错误状态）；LLM 调用 span（`llm:*`，父=llm_analyze 节点）。
  - span 表字段对齐常见遥测导出格式（trace_id/span_id/parent/kind/status/耗时/attributes）；
    attributes 只存安全标量。**说明**：未接入 OpenTelemetry SDK/OTLP（无 collector 场景下收益有限），
    以自研 SQLite span 存储实现同等语义，JSON 导出结构与之兼容——此为对 plan.md 技术选型的显式替换，理由如上。
- **LLM 调用脱敏快照**（`llm_calls` 表扩展）：system/user prompt 与模型响应在入库前过 redact()，
  记录 call_id→span_id、finish_reason、usage、耗时。
- **工具结果落库**（`tool_results` 表）：tool/unit/status/output/error/耗时/attempts/span_id，幂等 upsert。
- **追溯链查询**（`observability/trace.py`）：`Comment(finding) → LLM Call（含脱敏 prompt/响应快照）→
  Work Unit（状态+指纹）→ Task（来源/输入指纹/SHA）` + 工具结果 + 全部 span。
- **`cra trace <finding_id>`** 命令：文本渲染 + `--export` JSON 导出；finding 跨任务重名时要求 `--task` 指定；
  导出再过一遍 redact（纵深防御）。报告中每个 finding 增加 `追溯` 行（finding ID + trace 命令提示）。
- prompt 版本升至 1.1（trigger 字段入 Schema，版本化管理）。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **108 passed**（新增 trace 6 + CLI trace e2e 1） |
| 链完整性断言 | finding→llm_call（prompt 含 `<diff>` 脱敏快照、响应可解析）→work_unit(done)→task(指纹)；工具含 diff-stat/py-ast-check |
| span 层级断言 | 10 个节点 span 全 ok；2 个 llm span 父节点均为 node:llm_analyze |
| 脱敏断言 | prompt/导出 JSON 中 secret 原值不出现（`sk-live-...` grep 计 0），掩码 `[REDACTED:generic-secret]` 在场 |
| CLI 演示 | `cra trace <id>` 输出完整链；`--export examples/trace_example.json` 12 spans / 3 tools，secret-free |
| 结构化日志 | 每次 `runs/<task>/log.jsonl`（JSON lines：ts/level/logger/msg + 额外字段） |

### 已知限制 / 说明

- 结构化日志中的 `extra` 字段经过 JSON 序列化，但未做文本级 redact（当前 extra 只放计数/名称类安全标量，代码里已约束）。
- OTel OTLP 导出器未接入（自研 span 存储替代，见上）。
- `llm_calls` 表新增列对既有 DB 无迁移（`create_all` 不改表）；开发期删除 `runs/` 重建即可，见 Phase 11 遗留。

### 下一步

- Phase 7：Token/金额预算——单价表加载、调用前成本预留（含并发原子性）、实际用量结算、超限降级与部分报告。

## Phase 5 — 持久化 Checkpoint 与恢复（2026-09-24 完成）

### 完成项

- **LangGraph 持久化 Checkpointer**：`checkpoint/create_checkpointer`——SQLite saver（`langgraph-checkpoint-sqlite`），
  路径自动从 db 路径派生（`<db>.ckpt.sqlite`）；显式配置 msgpack 反序列化白名单
  （state 中的 pydantic 类型逐一登记，消除"未来版本将被禁止"的警告，也避免反序列化任意类的安全风险）。
- **单元级增量持久化**（`agent/nodes.py` + `persistence/ops.py`）：
  - `work_units` 表：每个工作单元完成/失败即落库（崩溃不丢已完成进度）；
  - `findings` 表：`finding_id` 改为确定性哈希 `(task_id|file|line|title)`，UNIQUE(task_id, finding_id) 约束 → 天然幂等；
  - `llm_calls` 表：每次模型调用的 usage/耗时入账（Phase 6 trace 与 Phase 7 预算的账本基础）；
  - `publications` 表：发布幂等键 `task_id:mode`，已有 dry_run/sent/confirmed 记录直接复用，不再重复发布。
- **`cra resume <task_id>` 命令**：
  - 任务不存在 / 已 stale → 明确报错退出；
  - 恢复前重新拉取输入并校验：本地 diff 比对 sha256 指纹，GitHub/GitLab 比对 head SHA；输入已变化 → 任务标记 `stale` 并要求新建任务；
  - 已完成且无失败单元 → 幂等 no-op（打印报告路径）；
  - 已完成但存在失败单元 → 只重试失败单元（已 done 单元跳过，不重复调用模型）；
  - 崩溃中断 → `graph.get_state().next` 判断存在未完成节点时从 checkpoint 续跑（`invoke(None, thread_id)`）；
    无 checkpoint（provider 阶段即失败）→ 完整重跑（llm_analyze 仍跳过已完成单元）。
- `review` 失败时输出 `可恢复: cra resume <task_id>` 提示。
- 修复 report.py 对 agent 包的反向依赖（循环导入）。

### 关键设计决策

- 恢复的"是否重跑"由**数据库中的单元状态**决定而非仅 LangGraph checkpoint：崩溃可能发生在节点内部
  （llm_analyze 循环中途），checkpoint 只覆盖节点边界，单元级进度必须由我们自己的账本承载。
  两层机制并存：checkpoint 负责节点边界，work_units/findings 账本负责单元边界。
- 重试"已完成任务中的失败单元"用 `invoke(新输入, 同 thread)`：节点重跑但 LLM 只为失败单元调用
  （幂等由账本保证），这是"预算不浪费"与"恢复完整性"的折中。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **102 passed**（新增 recovery 4：崩溃续跑不重做已完成单元、CLI 失败单元重试、输入变化 stale、早期失败后恢复成功） |
| 崩溃注入（llm_analyze 第 2 单元 KeyboardInterrupt） | 单元 1 完成落库（5 findings）；"重启进程"（新引擎/新连接/新 pipeline）后续跑，网关只被调用 1 次（未完成单元），最终 7 findings、发布记录唯一 |
| 重复 resume | 线程已在 END：直接返回状态，网关调用数不变、findings/publications 不重复 |
| CLI `resume`（stale 路径） | diff 被修改后 resume → exit 1，任务标记 stale |
| CLI 演示回归 | `review examples/buggy.diff` 7 findings；`resume <id>` 已完成任务 → no-op；`runs/cra.ckpt.sqlite` 12 个 checkpoint |

### 遗留 / 下一步

- Phase 6：评论级 trace——`Comment → Finding → LLM Call → prompt/响应快照` 的可查询关联、`cra trace` 命令、
  trace 导出与敏感数据过滤（llm_calls 表已有账目，补 prompt/响应安全快照与 span 层级）。

## Phase 4 — 声明式工具系统（2026-09-24 完成）

### 完成项

- `tools/base.py` `BaseReviewTool` 统一接口：名称、描述、scope（task/unit）、适用文件类型
  （fnmatch）、`requires_execution` 权限标记、超时、输入/输出契约（input_keys/output_keys）、
  统一 `ToolResult`（含 status/output/error/duration_ms/attempts）。
- `tools/dispatcher.py` `ToolDispatcher`：
  - **执行门禁**：`requires_execution=true` 且 sandbox 未启用 → 入口直接拒绝（skipped），LLM/配置都无法绕过；
  - 文件类型过滤；task 作用域跑一次、unit 作用域按工作单元跑；
  - 超时（线程池 + future timeout，超时返回 timeout 状态不重试）；
  - 失败隔离与受控重试（retries 来自 yaml；异常捕获成 failure ToolResult，不影响其他工具/单元）；
  - 已知限制：线程无法强杀，超时线程可能在后台结束；进程级隔离在 Phase 9 沙箱提供（文档已注明）。
- `build_dispatcher(tools.yaml)`：声明式装配——enabled 开关、timeout 覆盖、`sandbox.enabled` 总开关、
  `dispatcher.retries`。新增工具 = `@install` 类 + yaml 一行，主流程零改动（有测试证明）。
- 新增真实静态工具 `py-ast-check`（unit 作用域）：对**全量新增**的 Python 文件做 AST 语法检查，
  行号映射精确（含分块偏移）；修改类文件返回 skipped 并说明原因（缺完整内容，AST 不适用）。
- 示例执行型工具 `typecheck`：注册但默认 disabled；即使误启用，Dispatcher 门禁 + run() 双重拒绝执行，
  绝不在宿主机跑命令。
- `configs/tools.yaml` 重构：sandbox/dispatcher/tools 三段式声明。
- 报告中工具结果渲染 output 与 error 双信息。

### 修复的重要 Bug

- **redactor 误掩码导致代码损坏**：旧 generic-secret 模式把 `token = issue_token(username)`
  的函数调用误判为 secret，输出 `token = [REDACTED:generic-secret])`（残留右括号），
  py-ast-check 因此误报语法错误。重写为双模式：带引号值（允许 base64 padding）与
  裸值（值中排除括号/等号 + 语句边界 lookahead），函数调用与 `== None` 不再误报。
  该 bug 由 CLI 演示 + AST 工具组合发现，说明工具层交叉验证有效。

### 实际执行的验证

| 命令 | 结果 |
|---|---|
| `.venv/Scripts/python -m pytest tests/` | **98 passed**（工具系统 11：yaml 装配/新工具零改动接入/执行门禁/失败隔离+重试/超时/unit 文件过滤/AST 真实检出/修改文件跳过；redactor 新增 4 个防误报用例） |
| CLI `review examples/buggy.diff` | `diff-stat` success；`py-ast-check` success（auth.py）+ skipped（service.py，原因入报告）；findings 仍为 7 条 |
| CLI `review examples/syntax_error.diff` | AST 工具 failure：第 3 行语法错误（行号精确），流水线完成不中断（e2e 断言） |

### 遗留 / 下一步

- Phase 5：LangGraph 持久化 checkpointer + `resume <task_id>` + 幂等发布 key + commit SHA 校验。

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
