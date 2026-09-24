from __future__ import annotations

from code_review_agent.diff import FileChangeKind, parse_unified_diff


def test_parse_buggy_diff_files_and_kinds(buggy_diff):
    files = parse_unified_diff(buggy_diff)
    assert [f.path for f in files] == ["app/auth.py", "service.py"]
    assert files[0].kind == FileChangeKind.added
    assert files[1].kind == FileChangeKind.modified


def test_line_numbers_of_added_lines(buggy_diff):
    files = parse_unified_diff(buggy_diff)
    auth = files[0]
    added = {ln.new_line: ln.content for ln in auth.hunks[0].lines if ln.kind == "added"}
    assert added[3] == 'API_TOKEN = "sk-live-9f8e7d6c5b4a3210"'
    assert added[6] == "    result = subprocess.run(cmd, shell=True)"
    assert added[19] == "    return eval(expr)"
    assert len(added) == 19

    service = files[1]
    added2 = {ln.new_line: ln.content for ln in service.hunks[0].lines if ln.kind == "added"}
    assert added2[17] == "    def defaults(arg, value={}):"
    assert added2[19] == "    if user == None:"


def test_removed_line_keeps_old_line_number(buggy_diff):
    service = parse_unified_diff(buggy_diff)[1]
    removed = [ln for ln in service.hunks[0].lines if ln.kind == "removed"]
    assert removed[0].old_line == 15
    assert removed[0].new_line is None


def test_hunk_counts(buggy_diff):
    service = parse_unified_diff(buggy_diff)[1]
    h = service.hunks[0]
    assert (h.old_start, h.old_count) == (12, 5)
    assert (h.new_start, h.new_count) == (12, 10)


def test_binary_file_is_flagged():
    diff = "diff --git a/logo.png b/logo.png\nindex 111..222 100644\nBinary files a/logo.png and b/logo.png differ\n"
    files = parse_unified_diff(diff)
    assert files[0].kind == FileChangeKind.binary
    assert files[0].hunks == []


def test_deleted_file_is_flagged():
    diff = "diff --git a/old.py b/old.py\ndeleted file mode 100644\nindex 111..000\n--- a/old.py\n+++ /dev/null\n@@ -1,3 +0,0 @@\n-import os\n-import sys\n-print(1)\n"
    files = parse_unified_diff(diff)
    assert files[0].kind == FileChangeKind.deleted
    assert files[0].path == "old.py"
    assert all(ln.kind == "removed" for ln in files[0].hunks[0].lines)


def test_no_newline_marker_is_tolerated():
    diff = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n-a\n+b\n\\ No newline at end of file\n"
    files = parse_unified_diff(diff)
    assert len(files[0].hunks[0].lines) == 2


def test_preamble_noise_is_skipped():
    diff = "Some cover letter text.\ndiff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    files = parse_unified_diff(diff)
    assert len(files) == 1
    assert files[0].path == "x.py"
