# Code Review Agent

一个可恢复、可观测、可扩展、带成本预算与安全边界的 Code Review Agent。
输入 GitHub PR / GitLab MR / 本地 git diff，输出结构化 Review 报告（Markdown），
并在显式授权（`--publish`）后可回评行级评论到 PR/MR。

## 六项核心能力

| 能力 | 状态 | 位置 |
|---|---|---|
| Checkpoint 与异常恢复 | **已实现** | `checkpoint/`、`persistence/`、`agent/nodes.py` |
| 评论级 Trace 与可观测 | **已实现** | `observability/`、`persistence/`、`cra trace` |
| 声明式工具注册 | **已实现** | `tools/`、`configs/tools.yaml` |
| Token/金额预算 | **已实现** | `budget/`、`configs/model_pricing.yaml`、`--budget` |
| 置信度分级 | **已实现** | `review/validator.py`、`review/recheck.py` |
| Secret 防护与安全执行 | **已实现** | `security/`、`tools/sandbox.py` |
| GitHub/GitLab 行级评论发布 | **已实现**（未实测真实平台） | `publishers/`、`--publish` |

架构见 `docs/architecture.md`，安全设计见 `docs/security.md`，评估方法见 `docs/evaluation.md`，
各阶段实现与验证记录见 `docs/progress.md`。一键演示（无需 API Key）：`bash scripts/demo.sh`。

## 安装

要求 Python 3.11+。

```bash
cd code-review-agent
python -m venv .venv            # 或 uv venv --python 3.11 .venv
# Windows
.venv\Scripts\activate          # bash: source .venv/Scripts/activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

## 运行（无需任何 API Key，Mock 模型）

```bash
# 在项目根目录下执行
cra review examples/buggy.diff
# 指定输出位置
cra review examples/buggy.diff --report-dir runs --db runs/cra.sqlite
```

三种输入（GitHub/GitLab 适配器经 Mock API 验证，未用真实令牌实测）：

```bash
cra review examples/buggy.diff                                   # 本地 diff/patch 文件
cat fix.diff | cra review -                                      # 标准输入
cra review https://github.com/owner/repo/pull/123                # GitHub PR（需 GITHUB_TOKEN）
cra review https://gitlab.com/group/project/-/merge_requests/45  # GitLab MR（需 GITLAB_TOKEN）
```

输出：
- `runs/<task_id>/report.md` — 审查报告（按"高置信度/仅供参考"分组）
- `runs/<task_id>/log.jsonl` — 结构化执行日志
- `runs/cra.sqlite` — 任务与 findings 记录

发布行级评论（需显式授权，未实测真实平台）：

```bash
cra review https://github.com/owner/repo/pull/123 --publish   # GITHUB_TOKEN 已设置
cra review https://gitlab.com/group/proj/-/merge_requests/45 --publish  # GITLAB_TOKEN 已设置
```

发布前校验 PR/MR head SHA 与任务一致，只对能定位到新增行的 findings 发表评论
（无法定位的仅保留在报告），评论内嵌稳定锚点保证重复执行不重复发布。

## 恢复中断的任务

```bash
cra resume <task_id>   # task_id 见 review 输出或 runs/cra.sqlite 的 tasks 表
```

- 崩溃后从 checkpoint 续跑：已完成的工作单元直接跳过，不重复调用模型；
- 已完成任务中存在失败单元时，`resume` 只重试失败单元；
- 恢复前重新校验输入（本地 diff 指纹 / PR/MR head SHA）：输入已变化则任务标记 `stale` 并要求新建任务。

## 查询评论级 Trace

每条报告 finding 带 `追溯` 行（finding ID），可查询完整证据链：

```bash
cra trace <finding_id>                    # 终端查看：LLM 调用、脱敏 prompt/响应快照、工具结果、span 时间线
cra trace <finding_id> --export out.json  # 导出完整 JSON（导出内容再过一遍脱敏）
```

追溯链：`Comment → Finding → LLM Call（prompt/响应脱敏快照 + usage）→ Work Unit → Task（指纹/SHA）`，
外加工具结果与 span 层级。示例见 `examples/trace_example.json`。

## 成本预算

```bash
cra review examples/buggy.diff --budget 10.0    # 单任务预算上限（元，默认取 configs/agent.yaml）
```

- 每次模型调用前按单价表估算并**原子预留**，不足则不发起调用（LLM 无法绕过闸门）；
- 调用后按服务商返回的实际 usage 结算；usage 不可得时保守保留预留额；
- 预算耗尽 → 停止后续付费调用，产出**部分报告**（明确标注未审查范围）；
- 调高预算后 `cra resume <task_id>` 续审被跳过的单元；
- 未在 `configs/model_pricing.yaml` 登记单价的模型会被**拒绝调用**（fail-closed）。
- 报告中的金额为本地单价表估算口径，非服务商精确账单。

## 置信度分级

每条 finding 分为"高置信度（可直接采纳）"与"仅供参考"，分级由确定性代码决定（非 LLM 自评）：

- 高置信必须同时满足：行号指向本次**新增**代码、证据文本确实出现在 diff 中；
- 工具独立佐证（如 `py-ast-check` 检出的语法错误行）可支撑升级；
- 高置信候选在预算内做**上限复核**（`confirmed` 保持 / `uncertain` 降级 / `rejected` 移除）；
- 降级与复核理由写入报告、DB 与 trace（`cra trace <finding_id>`）。

## 安全边界

- **Secret 不出域**：diff 在进入模型/日志/trace 前脱敏；工具输出与模型回显入库前再脱敏；
  Git 平台令牌只从环境变量读取，绝不写入配置、日志或报告（有对抗性测试验证）。
- **fail-closed**：NUL/控制字符或非法 UTF-8 的输入直接拒绝处理；未登记单价的模型拒绝调用。
- **无任意代码执行**：默认只有纯文本工具；执行型工具（typecheck）只能在受限 Docker 沙箱内
  （无网络、非特权、只读、资源上限、不挂载宿主凭证）运行，命令固定、无 shell 拼接；
  docker 不可用时自动保持禁用。
- **Prompt Injection 防护**：diff/PR 描述/模型输出一律作为数据处理；工具选择、沙箱开关、
  脱敏与预算决策全部由确定性代码执行，模型与 diff 内容无权改变（对抗测试覆盖）。
- **默认不发布**：默认 dry-run；对 GitHub PR / GitLab MR 加 `--publish` 显式授权后发布行级评论
  （发布前校验 head SHA、只贴能定位到新增行的 findings、锚点幂等去重）。

## 接入真实 LLM（可选，已实测）

```bash
export OPENAI_API_KEY=sk-...                        # 任意 OpenAI 兼容服务；也可写入 .env（启动时自动加载）
export OPENAI_BASE_URL=https://api.openai.com/v1    # 可选，默认官方
cra review examples/buggy.diff --llm openai --model qwen3.8-flash
```

`--model` 指定模型 ID（该模型**必须在 `configs/model_pricing.yaml` 登记单价**，否则预算闸门
fail-closed 拒绝调用）；也可在 `configs/agent.yaml` 把 `model.provider` 改为 `openai` 并填写 `name`
（仓库默认保持 mock，无 Key 也能演示）。
凭证只从环境变量读取，绝不写入配置、日志或报告。真实 LLM 调用**已实测**
（qwen3.8-flash：6 次调用、预算结算 ¥0.0322、trace 脱敏 0 泄露，证据见 `docs/progress.md`）。

用真实模型审查 GitHub PR：

```bash
cra review https://github.com/owner/repo/pull/123 --llm openai --model qwen3.8-flash
```

## 测试

```bash
pytest
# 或
scripts/run_tests.sh
```

## 配置

- `configs/agent.yaml` — 模型、预算、发布模式、存储与输入限制
- `configs/tools.yaml` — 声明式工具注册（新增工具不改主流程）
- `configs/model_pricing.yaml` — 模型单价表（预算结算用）
- `.env.example` — 凭证模板；复制为 `.env` 后 CLI 启动时自动加载（KEY=VALUE，
  真实环境变量优先、不被覆盖；`.env` 已在 `.gitignore`，值绝不写日志/报告/DB）
