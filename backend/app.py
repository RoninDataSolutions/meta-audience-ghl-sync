import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import settings
from database import init_db
from scheduler import start_scheduler, shutdown_scheduler

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/app/logs/app.log", mode="a"),
    ] if os.path.isdir("/app/logs") else [logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting GHL-Meta Audience Sync application")
    init_db()
    logger.info("Database tables created/verified")
    start_scheduler()
    logger.info("Scheduler started")
    yield
    shutdown_scheduler()
    logger.info("Application shutdown")


app = FastAPI(title="GHL Meta Audience Sync", lifespan=lifespan)

# Register API routes
from routes.config_routes import router as config_router
from routes.sync_routes import router as sync_router
from routes.email_routes import router as email_router

app.include_router(config_router, prefix="/api")
app.include_router(sync_router, prefix="/api")
app.include_router(email_router, prefix="/api")

# Serve frontend static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = os.path.join(static_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=settings.WEB_PORT, reload=False)
