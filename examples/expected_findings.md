# 示例 `buggy.diff` 的预期审查结果

Mock 模型（`mock-reviewer-v1`）对 `examples/buggy.diff` 的确定性输出，
用于演示与回归验证（`tests/e2e/test_review_flow.py` 断言同一集合）。

共 7 条发现：高置信 3 条，仅供参考 4 条。

## 高置信度（可直接采纳）

| # | 位置 | 问题 | 严重性 | 触发条件 |
|---|---|---|---|---|
| 1 | `app/auth.py:6` | subprocess 使用 shell=True | critical | cmd 参数包含外部可控内容时 |
| 2 | `app/auth.py:19` | 使用 eval() 执行动态表达式 | critical | expr 来源于外部输入或字符串拼接 |
| 3 | `app/auth.py:3` | 疑似硬编码凭证 | high | 仓库被克隆或历史泄露即触发 |

注：#3 的原始值 `sk-live-9f8e7d6c5b4a3210` 在进入模型前被脱敏，
报告与 trace 中只出现 `[REDACTED:generic-secret]`。

## 仅供参考

| # | 位置 | 问题 | 严重性 | 触发条件 |
|---|---|---|---|---|
| 4 | `app/auth.py:15` | 异常被静默吞掉 | medium | 被捕获的代码路径抛出异常时 |
| 5 | `service.py:17` | 可变默认参数 | medium | 多次调用且未显式传入该参数时 |
| 6 | `app/auth.py:10` | 应使用 is/is not 比较 None | low | 操作数重载 __eq__ 时行为可能异常 |
| 7 | `service.py:19` | 应使用 is/is not 比较 None | low | 同上 |

`examples/clean.diff` 预期输出 0 条发现，报告显示"未发现可确认问题"。
