from __future__ import annotations

from code_review_agent.security.redactor import redact


def test_masks_aws_access_key():
    text = "aws_key = AKIAIOSFODNN7EXAMPLE"
    out, report = redact(text)
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws-access-key]" in out
    assert report.matches >= 1


def test_masks_generic_secret_assignment():
    text = 'API_TOKEN = "sk-live-9f8e7d6c5b4a3210"'
    out, report = redact(text)
    assert "sk-live-9f8e7d6c5b4a3210" not in out
    assert 'API_TOKEN = "[REDACTED:generic-secret]"' in out
    assert report.by_kind.get("generic-secret") == 1


def test_masks_private_key_header():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
    out, _ = redact(text)
    assert "BEGIN RSA PRIVATE KEY" not in out
    assert "[REDACTED:private-key]" in out


def test_normal_code_is_untouched():
    text = "def authenticate(user, password):\n    return user is not None\n"
    out, report = redact(text)
    assert out == text
    assert report.matches == 0


def test_redaction_preserves_line_count():
    text = 'a = 1\npassword = "super-secret-value"\nb = 2\n'
    out, _ = redact(text)
    assert out.count("\n") == text.count("\n")
