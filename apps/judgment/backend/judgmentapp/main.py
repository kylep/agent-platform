"""Judgment app entrypoint: FastAPI serving Kyle's API and the built frontend.
Nothing runs alongside it; Kai writes through the `judgment` tool."""
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from judgmentapp.api import router
from judgmentapp.db import init_db, make_engine, make_session_factory

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(os.environ.get("JUDGMENT_STATIC_DIR", "/app/static"))
PREFIX = "/apps/judgment"


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


@app.get(PREFIX, include_in_schema=False)
@app.get(PREFIX + "/{path:path}", include_in_schema=False)
async def frontend(path: str = ""):
    """The built frontend, with an SPA fallback: a static file if one is
    there, else index.html, so a deep link like /apps/judgment/beliefs/<id>
    survives a reload. Unknown API paths stay JSON 404s."""
    if path == "api" or path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    root = STATIC_DIR.resolve()
    if path:
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
    index = root / "index.html"
    if not index.is_file():
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(index)
