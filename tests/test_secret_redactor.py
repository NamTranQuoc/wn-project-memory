from src.services.secret_redactor import REDACTION_MASK, redact_mapping, redact_secrets

# Fake secrets, assembled at run time so secret scanners (gitleaks, GitHub) do not flag
# the source as a leak.
FAKE_OPENAI_KEY = "sk-" + "abcdefghijklmnopqrstuvwxyz" + "0123456789"
PEM_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
PEM_END = "-----END " + "PRIVATE KEY-----"


class TestSecretRedactor:
    def test_openai_key(self) -> None:
        out = redact_secrets(f"key={FAKE_OPENAI_KEY}")
        assert FAKE_OPENAI_KEY not in out
        assert REDACTION_MASK in out

    def test_openai_proj_key(self) -> None:
        token = "sk-proj-" + ("A" * 40)
        assert redact_secrets(f"OPENAI_API_KEY={token}") == (f"OPENAI_API_KEY={REDACTION_MASK}")

    def test_github_pat(self) -> None:
        token = "ghp_" + ("x" * 36)
        assert REDACTION_MASK in redact_secrets(f"token {token}")
        assert token not in redact_secrets(f"token {token}")

    def test_password_assignment(self) -> None:
        assert redact_secrets("password=s3cretValue!") == f"password={REDACTION_MASK}"
        assert redact_secrets('{"password": "s3cret"}') == (f'{{"password": "{REDACTION_MASK}"}}')

    def test_database_url_password(self) -> None:
        url = "postgresql://memory:superSecret@localhost:5432/memory_agent"  # privacy:allow — sample secret for the redactor
        out = redact_secrets(url)
        assert "superSecret" not in out
        assert f":{REDACTION_MASK}@" in out
        assert "memory@" not in out or "memory:" in out

    def test_sqlalchemy_asyncpg_uri_password(self) -> None:
        url = "postgresql+asyncpg://memory:superSecret@localhost:5432/memory_agent"  # privacy:allow — sample secret for the redactor
        out = redact_secrets(url)
        assert "superSecret" not in out
        assert f"postgresql+asyncpg://memory:{REDACTION_MASK}@" in out  # privacy:allow — sample secret for the redactor

    def test_sqlalchemy_psycopg_uri_password(self) -> None:
        url = "postgresql+psycopg://memory:dbPass99@db:5432/memory_agent"  # privacy:allow — sample secret for the redactor
        out = redact_secrets(url)
        assert "dbPass99" not in out
        assert f":{REDACTION_MASK}@" in out

    def test_bearer_header(self) -> None:
        out = redact_secrets("Authorization: Bearer abc.def.ghi_token")
        assert "abc.def.ghi_token" not in out
        assert f"Bearer {REDACTION_MASK}" in out

    def test_pem_private_key(self) -> None:
        pem = f"{PEM_BEGIN}\n" + "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" + f"\n{PEM_END}"
        assert redact_secrets(f"key:\n{pem}") == f"key:\n{REDACTION_MASK}"

    def test_aws_access_key(self) -> None:
        key = "AKIAIOSFODNN7EXAMPLE"  # privacy:allow — sample secret for the redactor
        assert key not in redact_secrets(f"aws_key={key}")

    def test_plain_text_untouched(self) -> None:
        text = "Use FastAPI with uv for dependency management."
        assert redact_secrets(text) == text

    def test_idempotent(self) -> None:
        once = redact_secrets(f"api_key={FAKE_OPENAI_KEY}")
        assert redact_secrets(once) == once


def test_mapping_masks_secret_keys_and_redacts_strings() -> None:
    config = {
        "chat_id": "19:payments",
        "jira_api_key": "abc",
        "github-token": "xyz",
        "tokenizer": "bge",
        "nested": [{"password": "p", "url": "postgresql://u:pw@db/x"}],  # privacy:allow — sample secret for the redactor
        "refresh_token": None,
    }
    assert redact_mapping(config) == {
        "chat_id": "19:payments",
        "jira_api_key": REDACTION_MASK,
        "github-token": REDACTION_MASK,
        "tokenizer": "bge",
        "nested": [{"password": REDACTION_MASK, "url": f"postgresql://u:{REDACTION_MASK}@db/x"}],
        "refresh_token": None,
    }
