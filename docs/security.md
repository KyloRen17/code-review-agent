# 安全设计

## 威胁模型

| 威胁 | 场景 | 防护 |
|---|---|---|
| Secret 泄露 | diff 中含凭证，被上传到 LLM/遥测/平台评论 | 五层脱敏（见下）；导出与评论渲染二次脱敏；对抗测试断言原值不出现在模型请求、报告、trace、日志 |
| Prompt Injection | diff/PR 描述/模型输出中嵌入指令，诱导改变行为 | 数据与指令分离：控制流决策（工具选择/沙箱/预算/脱敏）全部确定性代码；prompt 显式声明 `<diff>` 为被审数据；对抗测试证明注入无法启用 typecheck、无法绕过脱敏、无法产生注入驱动的 findings |
| 任意代码执行 | 恶意 diff 要求执行 shell | 默认只有纯文本工具；执行型工具仅限受限 Docker 沙箱且命令固定无拼接；沙箱不可用 → 保持禁用（fail-closed） |
| 凭证窃取 | API key / Git token 泄露 | 只从环境变量读取；不写入配置/日志/报告/DB；配置文件只含变量名 |
| 成本失控 | 模型调用失去预算约束 | 未登记单价 → 拒绝调用；调用前原子预留；超限停止付费调用 |
| 错位评论 | PR 更新后按旧 diff 发评论 | 发布前校验 head SHA；不匹配拒绝发布 |
| 重复写入 | 恢复/重试导致重复评论 | 本地 publications 记录 + 远端锚点两层幂等 |

## 脱敏层（红action 出口矩阵）

| 出口 | 时机 | 实现 |
|---|---|---|
| diff → 模型输入 | security_scan（解析前） | `security/redactor.py`（AWS key / 私钥 / 带引号与裸值 generic-secret；行内替换零行号漂移） |
| 工具输出 → DB | execute_tools 持久化时 | 序列化后再脱敏 |
| 模型回显 → finding | llm_analyze 构造 Finding 时 | evidence 再脱敏 |
| trace 导出 | `cra trace --export` | 导出 JSON 二次脱敏 |
| 平台评论 | `render_comment_body` | 渲染前脱敏（外发面） |

## fail-closed 点

- 输入含 NUL / 不可打印控制字符 → 拒绝处理（`assert_safe_text`）；
- 非 UTF-8 输入 → ProviderError（严格解码，不做有损替换）；
- 模型未在单价表登记 → 拒绝调用（成本失控防护）；
- 沙箱不可用 → 执行型工具禁用；
- PR/MR head SHA 不匹配 → 拒绝发布。

## 沙箱（执行型工具）

`tools/sandbox.py` `DockerSandboxRunner`：

- `--network none`（无网络）、`--read-only`（只读根）、`--cap-drop ALL` + `no-new-privileges`（非特权）、
  `--memory 512m --cpus 1 --pids-limit 64`（资源上限）、`--stop-timeout`（进程级强杀）；
- 待检文件按调用写入临时目录**只读挂载**（文件名取 basename 防路径穿越），不挂载任何宿主目录/凭证；
- 命令固定（`python -c "compile(...)"`），不含任何来自 diff/模型的内容。

本机无 Docker：沙箱自动保持禁用（日志告警），即"隔离无法验证则保持禁用"的验收行为本身。
沙箱逻辑经 FakeSandboxRunner 单测；真实容器执行**未实测**。

## 已知限制（诚实声明）

1. Secret 检测为正则模式集，非 gitleaks 全量规则（无熵值检测、无自定义规则加载）；
2. 沙箱与发布器均未经真实环境验证（本机无 Docker、无平台测试仓库与令牌）；
3. 跨进程的预算并发预留依赖 SQLite 文件锁，未做分布式账本；
4. OTel OTLP 导出未接入（自研 span 存储替代，见 architecture.md 决策 5）。

示例文件 `examples/buggy.diff` 中的 `sk-live-...` 是**故意放置的演示用假凭证**，用于验证脱敏链路。
