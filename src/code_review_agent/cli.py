from __future__ import annotations

import sys
import uuid
from pathlib import Path

import typer

from .agent.graph import build_review_graph
from .agent.nodes import ReviewPipeline
from .config import load_settings
from .llm import build_gateway
from .observability.logging import setup_logging
from .persistence.db import create_db_engine, init_db, make_session_factory
from .persistence.models import FindingRecord, TaskRecord
from .providers import build_provider, detect_source
from .publishers.dry_run import DryRunPublisher
from .tools.registry import build_registry

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Code Review Agent")


@app.callback()
def _root() -> None:
    """Code Review Agent — 可恢复、可观测、可扩展、带预算与安全边界的审查流水线。"""


@app.command()
def review(
    input_ref: str = typer.Argument(
        ..., help="本地 .diff/.patch 文件路径、'-'（标准输入）、GitHub PR 链接或 GitLab MR 链接"
    ),
    config: Path = typer.Option(Path("configs/agent.yaml"), "--config", help="agent 配置文件"),
    tools_config: Path = typer.Option(Path("configs/tools.yaml"), "--tools", help="工具声明配置"),
    db: Path = typer.Option(None, "--db", help="覆盖 SQLite 数据库路径"),
    report_dir: Path = typer.Option(None, "--report-dir", help="覆盖报告输出目录"),
    llm: str = typer.Option(None, "--llm", help="覆盖模型 provider（mock | openai）"),
):
    """审查输入 diff，生成 Markdown 报告。默认 dry-run，不做任何远程写入。"""
    settings = load_settings(config)
    if db is not None:
        settings.storage.db = str(db)
    if report_dir is not None:
        settings.storage.report_dir = str(report_dir)
    if llm is not None:
        settings.model.provider = llm

    task_id = uuid.uuid4().hex[:12]
    source = detect_source(input_ref)
    out_dir = Path(settings.storage.report_dir) / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(out_dir / "log.jsonl")

    engine = create_db_engine(settings.storage.db)
    init_db(engine)
    session_factory = make_session_factory(engine)

    with session_factory() as session:
        session.add(
            TaskRecord(
                id=task_id,
                source=source,
                input_ref=input_ref,
                status="running",
                model=settings.model.name,
            )
        )
        session.commit()

    gateway = build_gateway(settings)
    registry = build_registry(tools_config)
    provider = build_provider(input_ref, settings)
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=gateway,
        registry=registry,
        publisher=DryRunPublisher(),
        provider=provider,
        task_id=task_id,
    )
    graph = build_review_graph(pipeline)

    try:
        final = graph.invoke(
            {"task_id": task_id, "input_ref": input_ref, "source": source},
            config={"recursion_limit": 100},
        )
    except Exception as exc:
        with session_factory() as session:
            task = session.get(TaskRecord, task_id)
            if task is not None:
                task.status = "failed"
                task.error = str(exc)
                session.commit()
        typer.echo(f"任务失败: {exc}", err=True)
        raise typer.Exit(code=1)

    findings = final.get("validated_findings", [])
    with session_factory() as session:
        for f in findings:
            session.add(
                FindingRecord(
                    task_id=task_id,
                    finding_id=f.finding_id,
                    file=f.file,
                    line=f.line,
                    title=f.title,
                    severity=f.severity.value,
                    confidence=f.confidence.value,
                    description=f.description,
                    evidence=f.evidence,
                    suggestion=f.suggestion,
                    origin=f.origin,
                )
            )
        task = session.get(TaskRecord, task_id)
        if task is not None:
            task.status = "completed"
            task.fingerprint = final.get("input_fingerprint")
            task.report_path = final.get("report_path")
            task.error = "; ".join(final.get("errors", [])) or None
            session.commit()

    high = sum(1 for f in findings if f.confidence.value == "high")
    typer.echo(f"任务 {task_id} 完成")
    typer.echo(f"报告: {final.get('report_path')}")
    typer.echo(f"发现: {len(findings)} 条（高置信 {high} / 仅供参考 {len(findings) - high}）")
    receipt = final.get("publish_receipt", {})
    typer.echo(f"发布: {receipt.get('mode')} — {receipt.get('detail')}")
    errors = final.get("errors", [])
    if errors:
        typer.echo("警告:")
        for e in errors:
            typer.echo(f"  - {e}")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and stream.encoding and stream.encoding.lower() not in ("utf-8", "utf8"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except Exception:
                pass
    app()


if __name__ == "__main__":
    main()
