import enum

from sqlalchemy import Enum


class SourceType(str, enum.Enum):
    github_pr = "github_pr"
    github_repo = "github_repo"
    teams_chat = "teams_chat"
    teams_dm = "teams_dm"
    jira = "jira"
    local_file = "local_file"
    user_session = "user_session"
    other = "other"


class FactKind(str, enum.Enum):
    fact = "fact"
    decision = "decision"
    plan = "plan"
    question = "question"
    issue = "issue"
    solution = "solution"
    rule = "rule"
    task = "task"
    watched_ref = "watched_ref"


class ChangeType(str, enum.Enum):
    create = "create"
    replace = "replace"
    merge = "merge"
    close = "close"
    reopen = "reopen"


def pg_enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        create_constraint=True,
        values_callable=lambda e: [m.value for m in e],
    )
