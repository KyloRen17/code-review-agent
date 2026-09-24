# Code Review Agent

一个可恢复、可观测、可扩展、带成本预算与安全边界的 Code Review Agent。
输入 GitHub PR / GitLab MR / 本地 git diff，输出结构化 Review 报告（Markdown），
并在显式授权后可回评到 PR/MR（Phase 10）。

## 六项核心能力

| 能力 | 状态 | 位置 |
|---|---|---|
| Checkpoint 与异常恢复 | Phase 5 | `checkpoint/`、`persistence/` |
| 评论级 Trace 与可观测 | Phase 6 | `observability/`、`persistence/` |
| 声明式工具注册 | Phase 1（骨架）→ Phase 4（完整） | `tools/`、`configs/tools.yaml` |
| Token/金额预算 | Phase 7 | `budget/`、`configs/model_pricing.yaml` |
| 置信度分级 | Phase 1（最小）→ Phase 8（完整） | `review/validator.py` |
| Secret 防护与安全执行 | Phase 1（最小）→ Phase 9（完整） | `security/` |

当前进度见 `docs/progress.md`。

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

GitHub/GitLab 发布、预算闸门在后续 Phase 接入（见 `docs/progress.md`）。

## 接入真实 LLM（可选，已支持）

```bash
export OPENAI_API_KEY=sk-...                        # 任意 OpenAI 兼容服务
export OPENAI_BASE_URL=https://api.openai.com/v1    # 可选，默认官方
cra review examples/buggy.diff --llm openai
```

也可在 `configs/agent.yaml` 把 `model.provider` 改为 `openai` 并填写 `name`/`base_url`。
凭证只从环境变量读取，绝不写入配置、日志或报告；网关逻辑经 Mock 服务全链路验证，真实平台调用未实测。

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
- `.env.example` — 凭证模板；真实凭证只从环境变量读取，绝不入库
