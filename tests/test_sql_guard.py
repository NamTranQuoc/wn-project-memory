import pytest

from src.services.sql_service import ensure_limit, plan_relations, scope_to_project, validate_query


class TestValidate:
    def test_plain_select(self) -> None:
        q = "SELECT id, event_type FROM l4_raw_events WHERE event_type = 'pr'"
        assert validate_query(q) == q

    @pytest.mark.parametrize(
        "query",
        [
            "DELETE FROM l4_raw_events",
            "SELECT * FROM l3_facts",
            "SELECT * FROM l4_raw_events JOIN g_people ON true",
            "SELECT * FROM l4_raw_events; SELECT 1",
            "SELECT pg_read_file('/etc/passwd') FROM l4_raw_events",
            "SELECT * FROM l4_raw_events -- comment",
            "SELECT 1",
            "SELECT * INTO copy_t FROM l4_raw_events",
            "SELECT query_to_xml('select content from l3_facts', true, true, '') FROM l4_raw_events",
            (
                "SELECT query_to_xml('select pg_read_file(''/etc/hostname'')', true, true, '') "
                "FROM l4_raw_events"
            ),
            "SELECT pg_catalog.lower(event_type) FROM l4_raw_events",
            'SELECT "query_to_xml"(\'select 1\', true, true, \'\') FROM l4_raw_events',
        ],
    )
    def test_rejected(self, query: str) -> None:
        with pytest.raises(ValueError):
            validate_query(query)

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT event_type, count(*) FROM l4_raw_events GROUP BY event_type",
            (
                "SELECT id FROM l4_raw_events WHERE lower(raw_content) LIKE '%deploy%' "
                "AND created_at > now() - make_interval(days => 7)"
            ),
            (
                "SELECT id FROM l4_raw_events WHERE event_type IN ('pr', 'jira') "
                "AND EXISTS (SELECT 1 FROM l4_raw_events)"
            ),
            "SELECT row_number() OVER (ORDER BY created_at) FROM l4_raw_events",
        ],
    )
    def test_allowed_functions_and_keywords(self, query: str) -> None:
        assert validate_query(query) == query

    def test_keywords_inside_literals_are_data(self) -> None:
        q = "SELECT id FROM l4_raw_events WHERE raw_content ILIKE '%do not update%'"
        assert validate_query(q) == q


def test_scope_replaces_table() -> None:
    out = scope_to_project("SELECT id FROM l4_raw_events WHERE true")
    assert "WHERE project_path = :project_path" in out
    assert out.startswith("SELECT id FROM (SELECT * FROM l4_raw_events")


def test_limit_forced_and_capped() -> None:
    assert ensure_limit("SELECT 1 FROM l4_raw_events").endswith("LIMIT 10")
    assert ensure_limit("SELECT 1 FROM l4_raw_events LIMIT 500").endswith("LIMIT 10")
    assert ensure_limit("SELECT 1 FROM l4_raw_events LIMIT 3").endswith("LIMIT 3")


def test_plan_relations_walks_nested_plans() -> None:
    plan = [{"Plan": {"Node Type": "Append", "Plans": [
        {"Relation Name": "l4_raw_events_2026_10"},
        {"Plans": [{"Relation Name": "g_people"}]},
    ]}}]
    assert plan_relations(plan) == {"l4_raw_events_2026_10", "g_people"}
