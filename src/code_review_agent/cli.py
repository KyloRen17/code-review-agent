from __future__ import annotations

import sys
import uuid
from pathlib import Path

import typer
from sqlalchemy import select

from .agent.graph import build_review_graph
from .agent.nodes import ReviewPipeline
from .budget import BudgetController, BudgetLedger, PricingTable
from .checkpoint import create_checkpointer, has_checkpoint
from .config import load_settings
from .llm import build_gateway
from .observability.logging import setup_logging
from .persistence import ops
from .persistence.db import create_db_engine, init_db, make_session_factory
from .persistence.models import TaskRecord, WorkUnitRecord
from .providers import build_provider, detect_source
from .providers.base import ProviderError
from .publishers import build_review_publisher
from .tools.dispatcher import build_dispatcher

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Code Review Agent")


@app.callback()
def _root() -> None:
    """Code Review Agent — 可恢复、可观测、可扩展、带预算与安全边界的审查流水线。"""


def _prepare(settings):
    engine = create_db_engine(settings.storage.db)
    init_db(engine)
    return make_session_factory(engine)


def _load_settings_with_overrides(
    config: Path, db: Path | None, report_dir: Path | None, llm: str | None
):
    settings = load_settings(config)
    if db is not None:
        settings.storage.db = str(db)
    if report_dir is not None:
        settings.storage.report_dir = str(report_dir)
    if llm is not None:
        settings.model.provider = llm
    return settings


def _build_budget(settings, session_factory) -> BudgetController | None:
    pricing = PricingTable.load(settings.budget.pricing_file)
    if pricing is None:
        return None
    ledger = BudgetLedger(session_factory, settings.budget.limit, settings.budget.currency)
    return BudgetController(pricing, ledger)


def _execute(
    settings, tools_config: Path, input_ref: str, task_id: str, session_factory, *, fresh: bool,
    publisher=None,
):
    gateway = build_gateway(settings)
    dispatcher = build_dispatcher(tools_config)
    provider = build_provider(input_ref, settings)
    pipeline = ReviewPipeline(
        settings=settings,
        gateway=gateway,
        dispatcher=dispatcher,
        publisher=publisher or build_review_publisher(detect_source(input_ref), publish=False),
        provider=provider,
        task_id=task_id,
        session_factory=session_factory,
        budget=_build_budget(settings, session_factory),
    )
    checkpointer = create_checkpointer(settings.storage.checkpoint_db)
    graph = build_review_graph(pipeline, checkpointer=checkpointer)
    invoke_config = {
        "recursion_limit": 100,
        "configurable": {"thread_id": task_id},
    }
    initial = {
        "task_id": task_id,
        "input_ref": input_ref,
        "source": detect_source(input_ref),
    }
    if fresh:
        return graph.invoke(initial, invoke_config)
    if has_checkpoint(checkpointer, task_id):
        snapshot = graph.get_state(invoke_config)
        if snapshot.next:  # 存在未完成节点 → 从 checkpoint 续跑
            return graph.invoke(None, invoke_config)
    # 无 checkpoint（provider 阶段即失败）或已到 END（重试失败单元）→ 完整重跑，
    # 但 llm_analyze 会跳过 DB 中已完成的工作单元
    return graph.invoke(initial, invoke_config)


def _finalize(final: dict, task_id: str, session_factory) -> None:
    findings = final.get("validated_findings", [])
    with session_factory() as session:
        for f in findings:
            ops.save_finding(session, f)
        task = session.get(TaskRecord, task_id)
        if task is not None:
            task.status = "completed"
            task.fingerprint = final.get("input_fingerprint")
            task.base_sha = final.get("base_sha")
            task.head_sha = final.get("head_sha")
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


def _mark_failed(task_id: str, session_factory, error: str) -> None:
    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        if task is not None:
            task.status = "failed"
            task.error = error
            session.commit()


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
    budget: float = typer.Option(None, "--budget", help="覆盖单任务预算上限（元）"),
    publish: bool = typer.Option(False, "--publish", help="显式授权发布行级评论到 PR/MR（默认 dry-run）"),
):
    """审查输入 diff，生成 Markdown 报告。默认 dry-run，不做任何远程写入。"""
    settings = _load_settings_with_overrides(config, db, report_dir, llm)
    if budget is not None:
        settings.budget.limit = budget

    source = detect_source(input_ref)
    try:
        publisher = build_review_publisher(source, publish)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    task_id = uuid.uuid4().hex[:12]
    out_dir = Path(settings.storage.report_dir) / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(out_dir / "log.jsonl")

    session_factory = _prepare(settings)

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

    try:
        final = _execute(settings, tools_config, input_ref, task_id, session_factory, fresh=True, publisher=publisher)
    except Exception as exc:
        _mark_failed(task_id, session_factory, str(exc))
        typer.echo(f"任务失败: {exc}", err=True)
        typer.echo(f"可恢复: cra resume {task_id}", err=True)
        raise typer.Exit(code=1)
    _finalize(final, task_id, session_factory)


@app.command()
def resume(
    task_id: str = typer.Argument(..., help="要恢复的任务 ID"),
    config: Path = typer.Option(Path("configs/agent.yaml"), "--config", help="agent 配置文件"),
    tools_config: Path = typer.Option(Path("configs/tools.yaml"), "--tools", help="工具声明配置"),
    db: Path = typer.Option(None, "--db", help="覆盖 SQLite 数据库路径"),
    report_dir: Path = typer.Option(None, "--report-dir", help="覆盖报告输出目录"),
    llm: str = typer.Option(None, "--llm", help="覆盖模型 provider（mock | openai）"),
    budget: float = typer.Option(None, "--budget", help="覆盖单任务预算上限（元）"),
    publish: bool = typer.Option(False, "--publish", help="显式授权发布行级评论到 PR/MR（默认 dry-run）"),
):
    """恢复中断/失败的任务：已完成单元跳过，失败/预算跳过单元安全重试。"""
    settings = _load_settings_with_overrides(config, db, report_dir, llm)
    if budget is not None:
        settings.budget.limit = budget
    session_factory = _prepare(settings)

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        if task is None:
            typer.echo(f"任务不存在: {task_id}", err=True)
            raise typer.Exit(code=1)
        if task.status == "stale":
            typer.echo(f"任务 {task_id} 已因输入变化标记 stale，请新建任务", err=True)
            raise typer.Exit(code=1)
        input_ref = task.input_ref

    log_path = Path(settings.storage.report_dir) / task_id / "log.jsonl"
    setup_logging(log_path)

    provider = build_provider(input_ref, settings)
    try:
        current = provider.fetch(input_ref)
    except ProviderError as exc:
        typer.echo(f"无法重新校验输入，恢复终止: {exc}", err=True)
        raise typer.Exit(code=1)

    with session_factory() as session:
        task = session.get(TaskRecord, task_id)
        fingerprint_changed = bool(task.fingerprint) and (current.fingerprint or "") != task.fingerprint
        sha_changed = (
            task.source in ("github", "gitlab")
            and bool(current.head_sha)
            and bool(task.head_sha)
            and current.head_sha != task.head_sha
        )
        if fingerprint_changed or sha_changed:
            task.status = "stale"
            task.error = "输入已变化（指纹或目标 SHA 不匹配），任务标记 stale"
            session.commit()
            typer.echo(
                f"输入已变化，任务 {task_id} 标记 stale；请对新输入重新创建任务", err=True
            )
            raise typer.Exit(code=1)
        if task.status == "completed":
            failed_units = session.scalars(
                select(WorkUnitRecord).where(
                    WorkUnitRecord.task_id == task_id,
                    WorkUnitRecord.status.in_(["failed", "skipped"]),
                )
            ).all()
            if not failed_units:
                typer.echo(f"任务 {task_id} 已完成，报告: {task.report_path}")
                raise typer.Exit(code=0)
            typer.echo(f"重试 {len(failed_units)} 个失败/跳过工作单元")
        task.status = "running"
        session.commit()

    typer.echo(f"恢复任务 {task_id}（{input_ref}）")
    try:
        publisher = build_review_publisher(detect_source(input_ref), publish)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    try:
        final = _execute(settings, tools_config, input_ref, task_id, session_factory, fresh=False, publisher=publisher)
    except Exception as exc:
        _mark_failed(task_id, session_factory, str(exc))
        typer.echo(f"恢复失败: {exc}", err=True)
        raise typer.Exit(code=1)
    _finalize(final, task_id, session_factory)


@app.command()
def trace(
    finding_id: str = typer.Argument(..., help="finding ID（见报告「追溯」行）"),
    task: str = typer.Option(None, "--task", help="限定任务 ID（finding 跨任务重名时需要）"),
    db: Path = typer.Option(None, "--db", help="覆盖 SQLite 数据库路径"),
    export: Path = typer.Option(None, "--export", help="导出完整 trace JSON 到指定文件"),
):
    """查询某条评论（finding）的完整追溯链：调用、prompt/响应脱敏快照、工具与 span。"""
    settings = load_settings(Path("configs/agent.yaml"))
    if db is not None:
        settings.storage.db = str(db)
    session_factory = _prepare(settings)
    from .observability import trace as trace_mod

    with session_factory() as session:
        task_ids = trace_mod.find_task_ids_for_finding(session, finding_id)
        if not task_ids:
            typer.echo(f"未找到 finding: {finding_id}", err=True)
            raise typer.Exit(code=1)
        if task is None and len(task_ids) > 1:
            typer.echo(
                f"finding {finding_id} 存在于多个任务: {', '.join(task_ids)}；请用 --task 指定",
                err=True,
            )
            raise typer.Exit(code=1)
        task_id = task or task_ids[0]
        chain = trace_mod.build_trace_chain(session, finding_id, task_id)
        spans = trace_mod.load_spans(session, task_id)

    typer.echo(trace_mod.render_trace_text(chain, spans))
    if export is not None:
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text(trace_mod.export_trace_json(chain, spans), encoding="utf-8")
        typer.echo(f"已导出: {export}")


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
