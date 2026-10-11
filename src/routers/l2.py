"""REST · L2 namespaces, key building and the fact index."""

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.routers.common import run
from src.services import namespace_service

router = APIRouter(prefix="/l2", tags=["L2"])
ProjectPath = Query(..., description="Absolute path of the project repo")


class NamespaceBody(BaseModel):
    key_template: str
    summary: str
    default_kind: str = "fact"
    keywords: list[str] = Field(default_factory=list)


class BuildKeyBody(BaseModel):
    namespace: str
    parts: dict[str, str]


@router.get("/namespaces")
async def list_namespaces(project_path: str = ProjectPath, query: str | None = None,
                          limit: int = 5, offset: int = 0) -> dict:
    return await run(lambda s: namespace_service.list_namespaces(
        s, project_path, query=query, limit=limit, offset=offset))


@router.put("/namespaces/{namespace}")
async def put_namespace(namespace: str, body: NamespaceBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: namespace_service.upsert_namespace(
        s, project_path, namespace=namespace, **body.model_dump()))


@router.post("/keys/build")
async def build_key(body: BuildKeyBody, project_path: str = ProjectPath) -> dict:
    return await run(lambda s: namespace_service.build_fact_key(
        s, project_path, namespace=body.namespace, parts=body.parts))


@router.get("/index/{namespace}")
async def fact_index(namespace: str, project_path: str = ProjectPath, status: str | None = None,
                     limit: int = 5, offset: int = 0) -> dict:
    return await run(lambda s: namespace_service.list_fact_index(
        s, project_path, namespace=namespace, status=status, limit=limit, offset=offset))
