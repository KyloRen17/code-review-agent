from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..security.redactor import SecurityError

_DOCKER_FLAGS = [
    "--rm",
    "--network", "none",     # 无网络
    "--read-only",            # 只读根文件系统
    "--memory", "512m",       # 内存上限
    "--cpus", "1",            # CPU 上限
    "--pids-limit", "64",
    "--cap-drop", "ALL",      # 去除全部 Linux capabilities（非特权）
    "--security-opt", "no-new-privileges",
]


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str


class SandboxRunner(Protocol):
    available: bool

    def run(self, command: list[str], timeout_s: float, files: dict[str, str] | None = None) -> SandboxResult: ...


class DockerSandboxRunner:
    """受限容器执行：无网络、非特权、只读、资源上限、不挂载宿主凭证。

    待处理文件按调用写入临时目录并以只读方式挂载到 /work；
    隔离不可验证（docker 不存在/守护进程不可用）时 available=False，
    执行型工具保持禁用（fail-closed to disabled）。
    """

    def __init__(self, image: str = "python:3.11-slim") -> None:
        self.image = image
        self.available = self._check()

    def _check(self) -> bool:
        docker = shutil.which("docker")
        if docker is None:
            return False
        try:
            result = subprocess.run(
                [docker, "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        return result.returncode == 0 and bool(result.stdout.strip())

    def run(
        self, command: list[str], timeout_s: float, files: dict[str, str] | None = None
    ) -> SandboxResult:
        if not self.available:
            raise SecurityError("沙箱不可用，拒绝执行命令（fail-closed）")
        docker = shutil.which("docker")
        assert docker is not None
        workdir: str | None = None
        cmd = [docker, "run", *_DOCKER_FLAGS]
        if files:
            workdir = tempfile.mkdtemp(prefix="cra-sandbox-")
            for name, content in files.items():
                safe_name = Path(name).name  # 只用文件名，防路径穿越
                (Path(workdir) / safe_name).write_text(content, encoding="utf-8")
            cmd += ["-v", f"{workdir}:/work:ro", "-w", "/work"]
        else:
            cmd += ["-w", "/tmp"]
        cmd += ["--stop-timeout", str(int(max(1, timeout_s))), self.image, *command]
        try:
            completed = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s + 5
            )
        except subprocess.TimeoutExpired as exc:
            raise SecurityError(f"沙箱命令超时（>{timeout_s}s），已终止") from exc
        finally:
            if workdir:
                shutil.rmtree(workdir, ignore_errors=True)
        return SandboxResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class FakeSandboxRunner:
    """测试用：记录命令与文件但不执行，返回预设结果。"""

    available = True

    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.commands: list[list[str]] = []
        self.files: list[dict[str, str]] = []

    def run(
        self, command: list[str], timeout_s: float, files: dict[str, str] | None = None
    ) -> SandboxResult:
        self.commands.append(list(command))
        self.files.append(dict(files or {}))
        return SandboxResult(exit_code=self.exit_code, stdout=self.stdout, stderr=self.stderr)


__all__ = ["SandboxRunner", "SandboxResult", "DockerSandboxRunner", "FakeSandboxRunner", "SecurityError"]
