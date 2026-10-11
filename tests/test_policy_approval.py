"""Policy changes reach the database only after the user answers the MCP dialog.

Runs the real tool through an MCP client, on both protocol generations: the mid-call
elicitation request (initialize handshake) and the input-required round trip (2026-07-28).
"""

import json

import pytest
from mcp import types
from mcp.client import Client
from sqlalchemy import text

from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set")

P = "/work/payments"
MODES = ["legacy", "2026-07-28"]


@pytest.fixture(autouse=True)
async def clean_notes():
    from src.core.db import engine

    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE l1_notes, l1_sources, l1_projects CASCADE"))


def user_says(approve: bool | None):
    asked: list[str] = []

    async def callback(_context, params):
        asked.append(params.message)
        if approve is None:
            return types.ElicitResult(action="decline")
        return types.ElicitResult(action="accept", content={"approve": approve})

    return callback, asked


async def upsert(mode: str, callback, **fields) -> dict:
    from src.mcp_server import mcp

    note = {"project_path": P, "note_key": "policy.write-gate", "title": "Write gate",
            "content": "Confirm before posting.", "is_policy": True, **fields}
    async with Client(mcp, mode=mode, elicitation_callback=callback) as client:
        result = await client.call_tool("upsert_project_note", note)
    return json.loads(result.content[0].text)


@pytest.mark.parametrize("mode", MODES)
async def test_policy_saved_only_after_approval(mode: str) -> None:
    declined, asked = user_says(None)
    assert (await upsert(mode, declined))["error"] == "policy_declined"
    assert "Add this binding policy" in asked[0] and "Confirm before posting." in asked[0]

    unticked, _ = user_says(False)
    assert (await upsert(mode, unticked))["error"] == "policy_declined"

    approved, _ = user_says(True)
    saved = await upsert(mode, approved)
    assert (saved["action"], saved["approved_via"]) == ("created", "mcp_elicitation")

    # Same text: nothing to approve, no dialog.
    silent, asked_again = user_says(True)
    assert (await upsert(mode, silent))["action"] == "unchanged"
    assert asked_again == []


@pytest.mark.parametrize("mode", MODES)
async def test_client_without_elicitation_saves_nothing(mode: str) -> None:
    assert (await upsert(mode, None))["error"] == "approval_unavailable"


async def test_plain_note_needs_no_dialog() -> None:
    callback, asked = user_says(True)
    out = await upsert("legacy", callback, note_key="build", is_policy=False)
    assert out["action"] == "created" and asked == []
