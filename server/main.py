import asyncio
import contextlib
import os
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from server.api_routes import router as api_router
from server.auth import get_or_create_default_user
from server.database import async_session_maker, close_db, init_db
from server.models import Agent
from server.tunnel import TunnelManager
from server.ui_routes import router as ui_router


structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


PORT = int(os.getenv("C2_PORT", "1324"))
tunnel_manager: TunnelManager | None = None


def _configure_cors(app: FastAPI) -> None:
    configured_origins = [
        origin.strip()
        for origin in os.getenv("CORS_ALLOW_ORIGINS", "").split(",")
        if origin.strip()
    ]
    allow_origin_regex = None
    if not configured_origins:
        allow_origin_regex = r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|https://[A-Za-z0-9-]+\.trycloudflare\.com$"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured_origins,
        allow_origin_regex=allow_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tunnel_manager

    logger.info("starting-noxveil")
    os.makedirs("./data", exist_ok=True)

    await init_db()
    logger.info("database-initialized")

    async with async_session_maker() as session:
        user = await get_or_create_default_user(session)
        logger.info("default-admin-ready", username=user.username, password_file="./data/initial_admin_password.txt")

    health_task = asyncio.create_task(check_agent_health(), name="agent-health-check")

    enable_tunnel = os.getenv("ENABLE_TUNNEL", "true").lower() == "true"
    app.state.tunnel_manager = None

    if enable_tunnel:
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        tunnel_manager = TunnelManager(local_port=PORT, data_dir=data_dir)
        app.state.tunnel_manager = tunnel_manager
        try:
            tunnel_url = await tunnel_manager.start()
            logger.info("tunnel-started", url=tunnel_url)
            print_banner(tunnel_url)
        except Exception as exc:
            logger.error("failed-to-start-tunnel", error=str(exc))
            print("\n[!] Failed to start tunnel. Running in local mode only.\n")
            print_local_banner()
    else:
        print_local_banner()

    logger.info("server-ready", port=PORT)

    try:
        yield
    finally:
        logger.info("shutting-down-server")
        health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_task

        if tunnel_manager:
            await tunnel_manager.stop()
            tunnel_manager = None

        await close_db()
        logger.info("server-stopped")


def print_banner(tunnel_url: str):
    banner = (
        "\n"
        "========================================\n"
        "  Noxveil\n"
        "========================================\n"
        f"Landing    : {tunnel_url}/\n"
        f"Tunnel URL : {tunnel_url}\n"
        f"Operator   : {tunnel_url}/login\n"
        "========================================\n"
    )
    print(banner)


def print_local_banner():
    banner = (
        "\n"
        "========================================\n"
        "  Noxveil\n"
        "========================================\n"
        f"Landing    : http://127.0.0.1:{PORT}/\n"
        f"Operator   : http://127.0.0.1:{PORT}/login\n"
        f"API        : http://127.0.0.1:{PORT}/api/v1\n"
        "Mode       : Local only\n"
        "========================================\n"
    )
    print(banner)


async def check_agent_health():
    while True:
        try:
            async with async_session_maker() as session:
                now = datetime.utcnow()
                result = await session.execute(select(Agent))
                agents = result.scalars().all()

                marked_alive = 0
                marked_dead = 0

                for agent in agents:
                    interval = agent.callback_interval or 5
                    threshold = now - timedelta(seconds=max(interval * 4, 45))
                    should_be_alive = bool(agent.last_seen and agent.last_seen >= threshold)

                    if agent.is_alive != should_be_alive:
                        agent.is_alive = should_be_alive
                        if should_be_alive:
                            marked_alive += 1
                        else:
                            marked_dead += 1

                if marked_alive or marked_dead:
                    await session.commit()
                    logger.info(
                        "agent-health-updated",
                        marked_alive=marked_alive,
                        marked_dead=marked_dead,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("health-check-error", error=str(exc))

        await asyncio.sleep(30)


app = FastAPI(
    title="Noxveil",
    description="Operations workspace for authorized cybersecurity labs",
    version="1.1.0",
    lifespan=lifespan,
)

_configure_cors(app)
app.include_router(api_router)
app.include_router(ui_router)


def _run_interactive_mode():
    import uvicorn

    from server.http_commander import commander

    local_tunnel_manager: TunnelManager | None = None
    enable_tunnel = os.getenv("ENABLE_TUNNEL", "true").lower() == "true"
    if enable_tunnel:
        local_tunnel_manager = TunnelManager(local_port=PORT, data_dir="./data")
        try:
            tunnel_url = asyncio.run(local_tunnel_manager.start())
            print_banner(tunnel_url)
            os.environ["ENABLE_TUNNEL"] = "false"
        except Exception as exc:
            print(f"[!] Failed to start tunnel: {exc}")
            print_local_banner()

    def run_server():
        uvicorn.run("server.main:app", host="0.0.0.0", port=PORT, reload=False, log_level="info")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    print(f"[*] Noxveil is listening on port {PORT}")
    print("[*] Waiting for interactive agent connection...\n")

    try:
        commander()
    finally:
        if local_tunnel_manager:
            asyncio.run(local_tunnel_manager.stop())


if __name__ == "__main__":
    import uvicorn

    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        _run_interactive_mode()
    else:
        logger.info("starting-uvicorn", port=PORT)
        uvicorn.run("server.main:app", host="0.0.0.0", port=PORT, reload=False, log_level="info")
