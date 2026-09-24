from __future__ import annotations

from code_review_agent.diff import DiffFile, DiffHunk, DiffLine
from code_review_agent.review.finding import Confidence, Finding, Severity
from code_review_agent.review.validator import validate_and_grade


def _hunk() -> DiffHunk:
    return DiffHunk(
        header="@@ -1,2 +1,3 @@",
        old_start=1,
        old_count=2,
        new_start=1,
        new_count=3,
        lines=[
            DiffLine(kind="context", old_line=1, new_line=1, content="ctx"),
            DiffLine(kind="added", new_line=2, content="added"),
            DiffLine(kind="context", old_line=2, new_line=3, content="ctx2"),
        ],
    )


def _files() -> list[DiffFile]:
    return [DiffFile(old_path="app.py", new_path="app.py", hunks=[_hunk()])]


def _finding(**overrides) -> Finding:
    base = dict(
        finding_id="f1",
        task_id="t1",
        file="app.py",
        line=2,
        title="t",
        description="d",
        evidence="added",
        severity=Severity.high,
        confidence=Confidence.high,
    )
    base.update(overrides)
    return Finding(**base)


def test_valid_finding_is_kept_with_high_confidence():
    kept, dropped = validate_and_grade("t1", [_finding()], _files())
    assert len(kept) == 1
    assert kept[0].confidence == Confidence.high
    assert dropped == []


def test_finding_for_file_outside_diff_is_dropped():
    kept, dropped = validate_and_grade("t1", [_finding(file="other.py")], _files())
    assert kept == []
    assert "不在本次 diff" in dropped[0]


def test_finding_with_out_of_range_line_is_dropped():
    kept, dropped = validate_and_grade("t1", [_finding(line=99)], _files())
    assert kept == []
    assert "超出" in dropped[0]


def test_high_confidence_without_line_is_downgraded():
    kept, _ = validate_and_grade("t1", [_finding(line=None)], _files())
    assert kept[0].confidence == Confidence.reference
    assert "行号" in kept[0].downgrade_reason


def test_high_confidence_without_evidence_is_downgraded():
    kept, _ = validate_and_grade("t1", [_finding(evidence="  ")], _files())
    assert kept[0].confidence == Confidence.reference
    assert "证据" in kept[0].downgrade_reason


def test_duplicates_are_dropped():
    two = [_finding(finding_id="a"), _finding(finding_id="b")]
    kept, dropped = validate_and_grade("t1", two, _files())
    assert len(kept) == 1
    assert "重复" in dropped[0]


def test_same_line_different_title_kept():
    two = [_finding(finding_id="a", title="one"), _finding(finding_id="b", title="two")]
    kept, _ = validate_and_grade("t1", two, _files())
    assert len(kept) == 2  # 标题相似度低，不合并


def test_high_confidence_requires_evidence_in_diff():
    f = _finding(finding_id="a", evidence="totally unrelated text")
    kept, _ = validate_and_grade("t1", [f], _files())
    assert kept[0].confidence == Confidence.reference
    assert "证据文本与 diff 不匹配" in kept[0].downgrade_reason


def test_high_confidence_requires_added_line():
    # line=1 是 context 行（未变更），不是本次新增
    f = _finding(finding_id="a", line=1, evidence="ctx")
    kept, _ = validate_and_grade("t1", [f], _files())
    assert kept[0].confidence == Confidence.reference
    assert "指向未变更行" in kept[0].downgrade_reason


def test_high_confidence_kept_when_position_and_evidence_valid():
    f = _finding(finding_id="a", line=2, evidence="added")
    kept, _ = validate_and_grade("t1", [f], _files())
    assert kept[0].confidence == Confidence.high
    assert kept[0].downgrade_reason is None


def test_similar_titles_are_merged():
    two = [
        _finding(finding_id="a", line=2, title="硬编码凭证泄露风险"),
        _finding(finding_id="b", line=2, title="硬编码的凭证泄露风险!"),
    ]
    kept, dropped = validate_and_grade("t1", two, _files())
    assert len(kept) == 1
    assert any("高度相似" in d for d in dropped)


def test_dissimilar_titles_not_merged():
    two = [
        _finding(finding_id="a", line=2, title="硬编码凭证"),
        _finding(finding_id="b", line=3, title="空指针风险提示"),
    ]
    kept, _ = validate_and_grade("t1", two, _files())
    assert len(kept) == 2


def test_tool_evidence_upgrades_reference_to_high():
    from code_review_agent.tools.base import ToolResult, ToolStatus

    units = [type("U", (), {"unit_id": "t:x#h0", "file": "app.py"})()]
    tool = ToolResult(
        tool="py-ast-check",
        work_unit_id="t:x#h0",
        status=ToolStatus.failure,
        output={"syntax_ok": False, "errors": [{"line": 2, "message": "bad syntax"}]},
    )
    f = _finding(finding_id="a", line=2, evidence="added", confidence=Confidence.reference)
    kept, _ = validate_and_grade("t1", [f], _files(), tool_results=[tool], work_units=units)
    assert kept[0].confidence == Confidence.high
    assert kept[0].tool_evidence == ["py-ast-check"]
