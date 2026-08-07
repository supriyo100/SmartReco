import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.routes import router as admin_router
from app.agent.mesh import check_models_at_startup
from app.auth.deps import identity_middleware
from app.auth.routes import router as auth_router
from app.catalog.routes import router as catalog_router
from app.chat.routes import router as chat_router
from app.config import settings
from app.profiles.routes import router as profile_router
from app.tracking.queue import writer_loop
from app.tracking.routes import router as tracking_router
from app.web.routes import router as web_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    writer = asyncio.create_task(writer_loop())
    scheduler = None
    if settings.SCHEDULER_ENABLED:
        from app.scheduler.jobs import build_scheduler  # noqa: WPS433
        scheduler = build_scheduler()
        scheduler.start()
    if settings.use_mesh:
        asyncio.create_task(check_models_at_startup())
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    writer.cancel()


app = FastAPI(title="SmartReco", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Runs before every route: fills request.state.{user_id, role, session_id},
# which /api/events already reads. Must be registered before the routers.
app.middleware("http")(identity_middleware)

app.include_router(auth_router)
app.include_router(admin_router)     # every route behind require_admin (§1.1)
app.include_router(web_router)
app.include_router(profile_router)
app.include_router(tracking_router)
app.include_router(chat_router)
app.include_router(catalog_router)   # last: owns "/" and "/course/{slug}"


@app.get("/healthz")
@app.get("/health")   # ▲B9 alias — deploy platforms probe either
async def healthz():
    return {"ok": True, "env": settings.ENV}
