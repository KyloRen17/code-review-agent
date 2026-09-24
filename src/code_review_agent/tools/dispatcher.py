from __future__ import annotations

import fnmatch
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from .base import BaseReviewTool, ToolResult, ToolScope, ToolStatus
from .registry import ToolRegistry, load_tools_config
from .sandbox import SandboxRunner

_EXECUTION_REFUSED = (
    "执行型工具（requires_execution）必须在沙箱内运行；沙箱不可用或未启用，已拒绝执行"
)


class ToolDispatcher:
    """统一调度器：文件类型过滤、超时、失败隔离与受控重试。

    决策全部由本类确定（不经过 LLM）。执行型工具只有在沙箱可用时才可能运行；
    沙箱不可用 → fail-closed 保持禁用，任何 diff/模型内容都无法改变该决策。
    超时通过线程池实现：线程无法被强杀，超时的调用会返回 timeout 状态但线程
    可能在后台结束——进程级强杀由沙箱（docker --stop-timeout）提供。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        retries: int = 0,
        sandbox: SandboxRunner | None = None,
    ) -> None:
        self.registry = registry
        self.retries = retries
        self.sandbox = sandbox
        self._executor: ThreadPoolExecutor | None = None

    @property
    def sandbox_enabled(self) -> bool:
        return self.sandbox is not None and self.sandbox.available

    def enabled_names(self) -> list[str]:
        return self.registry.names()

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")
        return self._executor

    def run_all(
        self, *, task_id: str, diff_files: list, work_units: list
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        for name in self.enabled_names():
            tool = self.registry.get(name)
            if tool is None:
                results.append(
                    ToolResult(tool=name, status=ToolStatus.failure, error="工具未注册")
                )
                continue
            if tool.requires_execution and not self.sandbox_enabled:
                results.append(
                    ToolResult(tool=name, status=ToolStatus.skipped, error=_EXECUTION_REFUSED)
                )
                continue
            if tool.scope == ToolScope.task:
                results.append(
                    self._dispatch(
                        tool, {"task_id": task_id, "diff_files": diff_files, "work_units": work_units, "sandbox": self.sandbox}
                    )
                )
                continue
            for unit in work_units:
                if not self._file_matches(tool, unit.file):
                    continue
                df = next((f for f in diff_files if f.path == unit.file), None)
                results.append(
                    self._dispatch(
                        tool,
                        {
                            "task_id": task_id,
                            "diff_files": diff_files,
                            "work_units": work_units,
                            "unit": unit,
                            "diff_file": df,
                            "sandbox": self.sandbox,
                        },
                    )
                )
        return results

    def _dispatch(self, tool: BaseReviewTool, context: dict) -> ToolResult:
        attempts = 0
        last: ToolResult | None = None
        max_attempts = self.retries + 1
        while attempts < max_attempts:
            attempts += 1
            start = time.monotonic()
            try:
                future = self._pool().submit(tool.run, context)
                result = future.result(timeout=tool.timeout_s)
            except FutureTimeoutError:
                last = ToolResult(
                    tool=tool.name,
                    work_unit_id=self._unit_id(context),
                    status=ToolStatus.timeout,
                    error=f"工具执行超过 {tool.timeout_s}s 超时",
                )
                break  # 超时不重试：同参数大概率再次超时
            except Exception as exc:
                last = ToolResult(
                    tool=tool.name,
                    work_unit_id=self._unit_id(context),
                    status=ToolStatus.failure,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            duration_ms = int((time.monotonic() - start) * 1000)
            result.duration_ms = duration_ms
            result.attempts = attempts
            if result.status == ToolStatus.success:
                return result
            last = result
            if result.status == ToolStatus.skipped:
                break  # 主动跳过不重试
        assert last is not None
        last.attempts = attempts
        return last

    @staticmethod
    def _unit_id(context: dict) -> str:
        unit = context.get("unit")
        return unit.unit_id if unit is not None else "*"

    @staticmethod
    def _file_matches(tool: BaseReviewTool, file: str) -> bool:
        return any(fnmatch.fnmatch(file, pattern) for pattern in tool.file_types)


def build_dispatcher(tools_yaml: Path | str | None = None, sandbox: SandboxRunner | None = None) -> ToolDispatcher:
    """按声明式配置装配：enabled 工具进注册表，timeout 可在 yaml 覆盖，
    执行型工具受沙箱可用性约束（不可用即禁用）。新增工具不需要改动任何调用方。"""
    from . import implementations  # noqa: F401  触发工具自注册
    from .registry import _INSTALLED

    data = load_tools_config(tools_yaml)
    declared = data.get("tools", {}) or {}
    registry = ToolRegistry()
    for name, spec in declared.items():
        if not isinstance(spec, dict) or not spec.get("enabled", False):
            continue
        tool = _INSTALLED.get(name)
        if tool is None:
            continue
        if "timeout_s" in spec:
            tool.timeout_s = float(spec["timeout_s"])
        registry.register(tool)
    retries = int(data.get("dispatcher", {}).get("retries", 0))
    runner = sandbox
    if runner is None and data.get("sandbox", {}).get("enabled", False):
        runner = _build_default_sandbox(data.get("sandbox", {}))
    return ToolDispatcher(registry, retries=retries, sandbox=runner)


def _build_default_sandbox(sandbox_cfg: dict) -> SandboxRunner | None:
    """sandbox.enabled=true 时尝试 Docker 沙箱；隔离无法验证则保持禁用。"""
    import logging

    from .sandbox import DockerSandboxRunner

    image = str(sandbox_cfg.get("image", "python:3.11-slim"))
    runner = DockerSandboxRunner(image=image)
    if not runner.available:
        logging.getLogger("cra.tools").warning(
            "sandbox_unavailable",
            extra={"reason": "docker 不可用；执行型工具保持禁用（fail-closed）"},
        )
        return None
    return runner
