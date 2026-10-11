"""Test settings are fixed before any src module is imported.

Integration tests need TEST_DATABASE_URL (asyncpg URL of a throwaway database with
migrations applied); without it they are skipped.
"""

import os
import secrets

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("MEMORY_API_KEY", secrets.token_urlsafe(32))
os.environ["SEMANTIC_SEARCH_ENABLED"] = "false"
