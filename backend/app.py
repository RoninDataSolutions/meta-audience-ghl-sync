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


def _run_migrations():
    """Add new columns to existing tables without Alembic."""
    from sqlalchemy import text
    from database import engine
    migrations = [
        "ALTER TABLE ad_accounts ADD COLUMN IF NOT EXISTS website_url TEXT",
        "ALTER TABLE ad_accounts ADD COLUMN IF NOT EXISTS business_profile JSONB",
        # Conversion tracking tables (create_all handles new tables; these catch column additions)
        "ALTER TABLE stripe_transactions ADD COLUMN IF NOT EXISTS refunded_amount INTEGER DEFAULT 0",
        "ALTER TABLE stripe_transactions ADD COLUMN IF NOT EXISTS refund_date TIMESTAMP",
    ]
    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except Exception as e:
                logger.warning(f"Migration skipped: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting GHL-Meta Audience Sync application")
    init_db()
    _run_migrations()
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
from routers.accounts import router as accounts_router
from routers.audit import router as audit_router
from routers.conversions import router as conversions_router
from routers.heatmap import router as heatmap_router

app.include_router(config_router, prefix="/api")
app.include_router(sync_router, prefix="/api")
app.include_router(email_router, prefix="/api")
app.include_router(accounts_router, prefix="/api")
app.include_router(audit_router, prefix="/api")
app.include_router(conversions_router, prefix="/api")
app.include_router(heatmap_router, prefix="/api")

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
