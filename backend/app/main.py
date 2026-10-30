"""FastAPI entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker
from starlette.concurrency import run_in_threadpool

from app.api.router import api_router
from app.core.config import Settings
from app.database import build_engine, initialize_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_environment()
    engine = build_engine(settings)
    try:
        await run_in_threadpool(initialize_database, engine, settings)
        app.state.engine = engine
        app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        yield
    finally:
        await run_in_threadpool(engine.dispose)


app = FastAPI(title="Purchase Agent API", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
