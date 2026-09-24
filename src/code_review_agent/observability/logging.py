from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

_RESERVED = {"name", "msg", "args", "levelname", "levelno", "filename", "lineno", "funcName", "created", "asctime", "msecs", "relativeCreated", "exc_info", "exc_text", "stack_info", "taskName", "module", "pathname", "process", "processName", "thread", "threadName"}


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(log_path: Path | str, level: int = logging.INFO) -> None:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(JsonLineFormatter())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.addHandler(file_handler)
    root.addHandler(console)
