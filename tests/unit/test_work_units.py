from __future__ import annotations

from code_review_agent.agent.work_units import build_work_units
from code_review_agent.diff import DiffFile, DiffHunk, DiffLine, FileChangeKind, parse_unified_diff
from code_review_agent.llm.gateway import LLMRequest
from code_review_agent.llm.mock import MockLLMGateway
from code_review_agent.llm.schemas import parse_llm_findings


def _big_file(total_lines: int) -> DiffFile:
    lines = [
        DiffLine(kind="added", new_line=i, content=f"v{i} = {i}") for i in range(1, total_lines + 1)
    ]
    hunk = DiffHunk(
        header=f"@@ -0,0 +1,{total_lines} @@",
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=total_lines,
        lines=lines,
    )
    return DiffFile(old_path="big.py", new_path="big.py", hunks=[hunk])


def test_oversized_hunk_is_split_into_chunks():
    df = _big_file(500)
    units, notes = build_work_units("t", [df], max_unit_bytes=2000)
    assert len(units) > 1
    assert units[0].unit_id == "t:big.py#h0c0"
    assert units[0].start_line == 1
    assert units[-1].end_line == 500
    starts = [u.start_line for u in units]
    ends = [u.end_line for u in units]
    assert starts == sorted(starts)
    for i in range(len(units) - 1):
        assert units[i].end_line < units[i + 1].start_line
    for u in units:
        assert len(u.content.encode("utf-8")) <= 2000 + 64  # header 余量
    assert any("切分" in n for n in notes)


def test_chunk_line_numbers_feed_mock_correctly():
    df = _big_file(500)
    # 在第 250 行注入一个 eval
    lines = df.hunks[0].lines
    lines[249] = DiffLine(kind="added", new_line=250, content="bad = eval(user_input)")
    units, _ = build_work_units("t", [df], max_unit_bytes=2000)
    target = [u for u in units if u.start_line <= 250 <= u.end_line]
    assert len(target) == 1
    request = LLMRequest(
        call_id="c",
        model="mock",
        system="s",
        prompt="p",
        context={"file": "big.py", "code": target[0].content, "new_start": target[0].start_line},
    )
    parsed = parse_llm_findings(MockLLMGateway().complete(request).content)
    eval_findings = [f for f in parsed.findings if "eval" in f.title]
    assert eval_findings and eval_findings[0].line == 250


def test_normal_hunk_stays_single_unit(buggy_diff):
    files = parse_unified_diff(buggy_diff)
    units, _ = build_work_units("t", files, max_unit_bytes=65536)
    assert len(units) == 2
    assert units[0].unit_id == "t:app/auth.py#h0"
    assert units[1].unit_id == "t:service.py#h0"


def test_hunk_with_only_removed_lines_is_skipped():
    hunk = DiffHunk(
        header="@@ -1,2 +0,0 @@",
        old_start=1,
        old_count=2,
        new_start=0,
        new_count=0,
        lines=[
            DiffLine(kind="removed", old_line=1, content="a"),
            DiffLine(kind="removed", old_line=2, content="b"),
        ],
    )
    df = DiffFile(old_path="gone.py", new_path="gone.py", hunks=[hunk])
    units, _ = build_work_units("t", [df], max_unit_bytes=65536)
    assert units == []


def test_renamed_file_is_parsed():
    diff = (
        "diff --git a/old_name.py b/new_name.py\n"
        "similarity index 100%\n"
        "rename from old_name.py\n"
        "rename to new_name.py\n"
    )
    files = parse_unified_diff(diff)
    assert files[0].kind == FileChangeKind.renamed
    assert files[0].old_path == "old_name.py"
    assert files[0].new_path == "new_name.py"
    assert files[0].path == "new_name.py"


def test_renamed_file_with_edits_is_reviewable():
    diff = (
        "diff --git a/old.py b/new.py\n"
        "similarity index 80%\n"
        "rename from old.py\n"
        "rename to new.py\n"
        "--- a/old.py\n"
        "+++ b/new.py\n"
        "@@ -1,2 +1,2 @@\n"
        " ctx\n"
        "-old = 1\n"
        "+new = eval('x')\n"
    )
    files = parse_unified_diff(diff)
    assert files[0].kind == FileChangeKind.renamed
    units, _ = build_work_units("t", files, max_unit_bytes=65536)
    assert len(units) == 1
    assert units[0].file == "new.py"
    assert units[0].start_line == 1
