from src.services.secret_redactor import REDACTION_MASK, redact_messages, redact_secrets


class TestSecretRedactor:
    def test_openai_key(self) -> None:
        text = "key=sk-abcdefghijklmnopqrstuvwxyz0123456789"
        out = redact_secrets(text)
        assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in out
        assert REDACTION_MASK in out

    def test_openai_proj_key(self) -> None:
        token = "sk-proj-" + ("A" * 40)
        assert redact_secrets(f"OPENAI_API_KEY={token}") == (
            f"OPENAI_API_KEY={REDACTION_MASK}"
        )

    def test_github_pat(self) -> None:
        token = "ghp_" + ("x" * 36)
        assert REDACTION_MASK in redact_secrets(f"token {token}")
        assert token not in redact_secrets(f"token {token}")

    def test_password_assignment(self) -> None:
        assert redact_secrets("password=s3cretValue!") == f"password={REDACTION_MASK}"
        assert redact_secrets('{"password": "s3cret"}') == (
            f'{{"password": "{REDACTION_MASK}"}}'
        )

    def test_database_url_password(self) -> None:
        url = "postgresql://memory:superSecret@localhost:5432/memory_agent"
        out = redact_secrets(url)
        assert "superSecret" not in out
        assert f":{REDACTION_MASK}@" in out
        assert "memory@" not in out or "memory:" in out

    def test_sqlalchemy_asyncpg_uri_password(self) -> None:
        url = "postgresql+asyncpg://memory:superSecret@localhost:5432/memory_agent"
        out = redact_secrets(url)
        assert "superSecret" not in out
        assert f"postgresql+asyncpg://memory:{REDACTION_MASK}@" in out

    def test_sqlalchemy_psycopg_uri_password(self) -> None:
        url = "postgresql+psycopg://memory:dbPass99@db:5432/memory_agent"
        out = redact_secrets(url)
        assert "dbPass99" not in out
        assert f":{REDACTION_MASK}@" in out

    def test_bearer_header(self) -> None:
        out = redact_secrets("Authorization: Bearer abc.def.ghi_token")
        assert "abc.def.ghi_token" not in out
        assert f"Bearer {REDACTION_MASK}" in out

    def test_pem_private_key(self) -> None:
        pem = (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC\n"
            "-----END PRIVATE KEY-----"
        )
        assert redact_secrets(f"key:\n{pem}") == f"key:\n{REDACTION_MASK}"

    def test_aws_access_key(self) -> None:
        key = "AKIAIOSFODNN7EXAMPLE"
        assert key not in redact_secrets(f"aws_key={key}")

    def test_plain_text_untouched(self) -> None:
        text = "Use FastAPI with uv for dependency management."
        assert redact_secrets(text) == text

    def test_redact_messages(self) -> None:
        messages = [
            {"role": "system", "content": "safe"},
            {"role": "user", "content": "password=hunter2"},
        ]
        out = redact_messages(messages)
        assert out[0]["content"] == "safe"
        assert out[1]["content"] == f"password={REDACTION_MASK}"
        # Original list not mutated
        assert messages[1]["content"] == "password=hunter2"

    def test_idempotent(self) -> None:
        once = redact_secrets("api_key=sk-abcdefghijklmnopqrstuvwxyz012345")
        assert redact_secrets(once) == once
