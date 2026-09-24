# 评估方法

## 测试分层

| 层 | 目录 | 内容 | 依赖 |
|---|---|---|---|
| 单元 | `tests/unit/` | 模型 Schema、diff 解析、脱敏、Mock LLM、配置、工具系统（注册/门禁/超时/重试/过滤）、validator 规则、复核、预算账本（含 20 线程并发）、发布器、trace | 无网络、无 API Key |
| 集成 | `tests/integration/` | GitHub Mock 全流水线、OpenAI Mock 服务器全流水线（含坏响应隔离）、预算耗尽部分报告 | httpx MockTransport |
| 端到端 | `tests/e2e/` | CLI 全链路（buggy/clean/syntax_error diff、失败路径、trace、--publish 门控） | CliRunner |
| 恢复 | `tests/recovery/` | 崩溃续跑（KeyboardInterrupt 注入 + 模拟重启）、失败单元重试、输入变化 stale、早期失败恢复 | 无 |
| 安全 | `tests/security/` | 注入对抗、secret 不出域、fail-closed、沙箱门禁、令牌防泄漏 | 无 |

**Mock 与真实明确区分**：所有 MockTransport/CliRunner/Mock LLM 测试均不触网；
真实集成（真实 LLM、真实 GitLab/GitHub、真实 Docker 沙箱）在本环境**未验证**，条件与命令见下。

## 六项能力核验指引

| 能力 | 命令 | 预期 |
|---|---|---|
| 恢复 | `pytest tests/recovery/` 或 `scripts/demo.sh` 第 3 步 | 崩溃后已完成单元不重跑；重复 resume 无重复输出 |
| Trace | `cra trace <finding_id>`（报告"追溯"行取 ID） | 完整链：调用/prompt 脱敏快照/工具/spans；`--export` JSON 无 secret |
| 工具扩展 | `pytest tests/unit/test_tools_system.py` | 新增工具 = `@install` + yaml 一行；主流程零改动 |
| 预算 | `pytest tests/unit/test_budget.py tests/integration/test_budget_pipeline.py` 或 demo 第 4 步 | 预留原子性、并发不超额、耗尽 → 部分报告 |
| 置信度 | `pytest tests/unit/test_validator.py tests/unit/test_recheck.py` | 无效行号/证据不匹配/复核驳回不进高置信 |
| 安全 | `pytest tests/security/` 或 demo 第 5/6 步 | secret 不出域；注入不改控制流；默认无远程写入 |

## 真实集成验证状态

| 项 | 状态 | 命令 |
|---|---|---|
| 真实 LLM | **已验证**（2026-09-24，qwen3.8-flash：6 次调用、已结算 ¥0.0322、trace 脱敏 0 泄露，证据见 progress.md） | `cra review examples/buggy.diff --llm openai --model qwen3.8-flash` |
| GitHub 真实读取 | **已验证**（2026-09-24，公开 PR 真实拉取含 SHA 元数据；私有仓库仍需 GITHUB_TOKEN） | `cra review https://github.com/o/r/pull/N` |
| GitHub 真实发布 | 测试仓库 + 授权 | `cra review ... --publish`（重复执行验证幂等） |
| GitLab 同理 | `GITLAB_TOKEN` | 同上（MR 链接） |
| 沙箱 | 本机 docker | 在 `configs/tools.yaml` 开启 `sandbox.enabled` 与 `typecheck.enabled` 后 review 全量新增文件 diff |

## 演示数据

- `examples/buggy.diff`：2 文件 7 个已知问题（3 高置信/4 参考）+ 1 个假 secret；
- `examples/clean.diff`：预期 0 findings（"未发现可确认问题"）；
- `examples/syntax_error.diff`：全量新增文件语法错误（py-ast-check 检出行号）；
- `examples/expected_findings.md`：预期结果矩阵；
- `examples/review_report.md`：demo 产出的样例报告；
- `examples/trace_example.json`：脱敏 trace 导出样例。
