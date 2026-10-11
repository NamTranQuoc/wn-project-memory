import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from src.core.auth import ApiKeyMiddleware
from src.core.config import get_settings
from src.core.db import engine
from src.core.scheduler import start_scheduler, stop_scheduler
from src.mcp_server import mcp
from src.routers import global_store, health, l1, l2, l3, l4

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger(__name__)


def _transport_security() -> TransportSecuritySettings:
    """Host/Origin allow-list for /mcp (DNS-rebinding protection)."""
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for host in get_settings().extra_mcp_hosts():
        hosts += [host, f"{host}:*"]
        origins += [f"https://{host}", f"https://{host}:*"]
    return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)


# Stateful sessions with SSE responses: a tool can then ask the user mid-call
# (MCP elicitation), which policy changes require.
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    transport_security=_transport_security(),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings().require_api_key()
    start_scheduler()
    async with mcp.session_manager.run():
        logger.info("Agentic memory started")
        yield
    stop_scheduler()
    await engine.dispose()


app = FastAPI(title="Agentic Memory", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(ApiKeyMiddleware)
app.include_router(health.router)
for router in (l1.router, l2.router, l3.router, l4.router, global_store.router):
    app.include_router(router)
# Same tools as REST, over MCP streamable HTTP. The API-key middleware covers it too.
app.router.routes.extend(mcp_app.routes)
