"""Samples are assembled at runtime so this file itself passes the privacy check."""

from scripts import privacy_check


def rules(text: str, denylist: list[str] | None = None) -> set[str]:
    return {f.rule for f in privacy_check.scan_text("x", text, denylist or [])}


def test_tokens() -> None:
    assert "api_token" in rules("token " + "gh" + "p_" + "x" * 36)
    assert "private_key" in rules("-----BEGIN " + "RSA PRIVATE KEY-----")


def test_secret_assignment_but_not_placeholders() -> None:
    assert "secret_assignment" in rules("MEMORY_API" + "_KEY=" + "Ab3" * 10)
    assert "secret_assignment" not in rules("MEMORY_API_KEY=<generated>")
    assert "secret_assignment" not in rules("MEMORY_API_KEY=")


def test_personal_data() -> None:
    assert "email" in rules("ping " + "someone" + "@" + "corp" + ".io")
    assert "email" not in rules("dana@example.com")
    assert "teams_link" in rules("https://teams." + "microsoft.com/l/chat/1")
    assert "jira_link" in rules("https://acme." + "atlassian.net/browse/PAY-1")
    assert "home_path" in rules("/Us" + "ers/someone/projects")
    assert "home_path" not in rules("/Users/<name>/projects")
    assert "url_credentials" in rules("postgresql://bob:" + "hunter2" + "@db.corp.io/x")
    assert "url_credentials" not in rules("postgresql://<user>:<password>@db/x")
    assert "url_credentials" not in rules("postgresql://memory:memory@localhost:5432/x")
    assert "secret_assignment" not in rules("var token = relationshipTokenGeometry(shape)")


def test_denylist_and_allow_marker() -> None:
    assert rules("ssh into my-box.example", ["my-box"]) == {"denylist"}
    assert rules("ssh into my-box.example  # privacy:" + "allow", ["my-box"]) == set()


def test_mask_hides_value() -> None:
    assert privacy_check.mask("secretvalue") == "se***"
