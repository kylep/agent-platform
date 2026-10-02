"""Judgment app entrypoint: FastAPI serving Kyle's API and the built frontend.
Nothing runs alongside it; Kai writes through the `judgment` tool."""
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from judgmentapp.api import router
from judgmentapp.db import init_db, make_engine, make_session_factory

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(os.environ.get("JUDGMENT_STATIC_DIR", "/app/static"))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    await init_db(engine)
    app.state.sf = make_session_factory(engine)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="judgment app", lifespan=lifespan)
app.include_router(router)

if STATIC_DIR.is_dir():
    app.mount("/apps/judgment", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
