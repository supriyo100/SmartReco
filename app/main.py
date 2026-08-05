import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.agent.mesh import check_models_at_startup
from app.config import settings
from app.tracking.queue import writer_loop
from app.tracking.routes import router as tracking_router

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
app.include_router(tracking_router)

# TODO wire as they're built (arch v2 §8 schedule):
# from app.auth.routes import router as auth_router        # Aug 5
# from app.admin.routes import router as admin_router      # Aug 5
# from app.catalog.routes import router as catalog_router  # Aug 5
# from app.web.routes import router as web_router          # Aug 6


@app.get("/healthz")
@app.get("/health")   # ▲B9 alias — deploy platforms probe either
async def healthz():
    return {"ok": True, "env": settings.ENV}
