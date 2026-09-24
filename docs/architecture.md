# 架构文档

## 总览

Code Review Agent 是一条**确定性编排**的 LangGraph 流水线：LLM 只负责"分析"，一切安全、
预算、验证与发布决策由代码执行，模型与 diff 内容无权改变控制流。

```text
输入（本地 diff / GitHub PR / GitLab MR）
   │  providers/（统一 ReviewInput + 指纹 + SHA）
   ▼
load_input ──► security_scan ──► normalize_diff ──► build_context
                 (fail-closed       (unified diff     (hunk/chunk 工作单元，
                  + secret 脱敏)      解析/行号映射)     增量落库)
                                                        │
   ┌────────────────────────────────────────────────────┘
   ▼
select_tools ──► execute_tools ──► llm_analyze ──► validate_findings ──► generate_report ──► publish
 (声明式注册)     (Dispatcher:        (每单元: 预算       (确定性规则:          (Markdown)         (默认 dry-run;
                  超时/重试/沙箱门)    预留→调用→结算;     行号/证据/新增行/
                                      增量持久化)         工具佐证/相似合并;
                                      复核: 预算内        预算内复核)
                                      有上限)
```

## 模块地图

| 模块 | 职责 |
|---|---|
| `agent/` | GraphState、节点（`nodes.py`）、图装配（`graph.py`）、工作单元（`work_units.py`） |
| `providers/` | 三种输入源 → 统一 `ReviewInput`；`ProviderError` 明确状态码 |
| `llm/` | 网关协议 + Mock + OpenAI 兼容实现；prompt 版本化；输出 Schema 强校验 |
| `tools/` | `BaseReviewTool`、注册表、Dispatcher（文件过滤/超时/重试/执行门禁）、沙箱 |
| `review/` | Finding 模型、确定性验证器、复核服务、Markdown 报告 |
| `budget/` | 单价表、账本（journal+汇总）、控制器（预留/结算/释放） |
| `checkpoint/` | LangGraph SQLite checkpointer（含 msgpack 白名单） |
| `persistence/` | SQLAlchemy 模型（tasks/work_units/findings/llm_calls/tool_results/spans/budget/publications）与 ops |
| `observability/` | JSON 日志、span 记录器、trace 链构建与导出 |
| `security/` | fail-closed 检查、secret 扫描与脱敏 |
| `publishers/` | dry-run / GitHub PR / GitLab MR 发布器（锚点幂等、SHA 校验） |

## 六项核心能力的实现位置

| 能力 | 实现要点 | 代码位置 |
|---|---|---|
| **可恢复** | 节点级：LangGraph SQLite checkpoint（thread=task_id）；单元级：work_units/findings/llm_calls 增量落库；恢复决策用 `graph.get_state().next` + DB 单元状态；发布幂等（publications 表 + 远端锚点）；输入变化 → stale | `checkpoint/`、`agent/nodes.py`、`cli.py resume`、`persistence/ops.py` |
| **可观测** | trace_id=task_id；节点/LLM/复核 span 层级（contextvar 父子）；prompt/响应/工具结果脱敏快照入 llm_calls/tool_results；`Comment→Finding→LLM Call→Work Unit→Task` 可查询链；`cra trace` + JSON 导出（二次脱敏） | `observability/`、`persistence/` |
| **可扩展** | `@install` + `configs/tools.yaml` 声明式注册；Dispatcher 统一调度（scope=task/unit、文件类型过滤、超时、失败隔离与受控重试）；新增工具零主流程改动（有测试证明） | `tools/` |
| **Token/金额预算** | 调用前按单价表+输入估算+max_output **原子预留**（线程锁+journal）；调用后按实际 usage 结算；usage 不可得保守保留；耗尽 → 后续单元 skipped + 部分报告；未登记单价 → 拒绝调用（fail-closed） | `budget/`、`agent/nodes.py`、`configs/model_pricing.yaml` |
| **置信度分级** | 确定性规则（新增行/证据匹配/工具佐证/相似合并）+ 预算内有上限复核（confirmed/uncertain/rejected）；LLM 自评只是输入信号；分级理由落 finding 字段（报告/DB/trace 可查） | `review/validator.py`、`review/recheck.py` |
| **安全** | fail-closed 输入检查；五层脱敏（diff→模型输入、工具输出、模型回显、trace 导出、发布评论）；执行型工具仅限受限 Docker 沙箱（不可用即禁用）；注入不可改变控制流；凭证只走环境变量；默认 dry-run + `--publish` 显式授权 | `security/`、`tools/sandbox.py`、`publishers/` |

## 关键设计决策

1. **脱敏先于解析**：security_scan 在 normalize_diff 之前执行，脱敏只做行内替换，
   保证行号零漂移（差异已在 progress.md Phase 1 说明）。
2. **双层恢复机制**：LangGraph checkpoint 覆盖节点边界；崩溃可能发生在节点内部
   （llm_analyze 循环中途），单元级进度由自有账本承载，两层缺一不可。
3. **确定性优先**：工具选择、沙箱开关、预算、脱敏、置信度分级全部由代码决策；
   LLM 输出必须过 Schema 校验，且其 confidence 不作为最终分级依据。
4. **幂等键设计**：finding_id = `sha1(task|file|line|title)`（确定性），发布锚点
   `<!-- CRA-FINDING:{id} -->`（远端去重），发布记录 `task:mode`（本地去重）。
5. **OTel 替代**：以 SQLite span 存储实现等价语义（trace/span/parent/kind/duration），
   JSON 导出结构与常见遥测格式兼容；未接 OTLP collector（无 collector 场景收益有限，已在 progress.md 声明）。
