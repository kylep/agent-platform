"""News app entrypoint: FastAPI serving the browse API + the built frontend,
with the Kafka ingest loop running alongside (docs/design/11)."""
import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from newsapp.api import router
from newsapp.db import init_db, make_engine, make_session_factory
from newsapp.ingest import IngestLoop
from newsapp.report import write_daily_report

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(os.environ.get("NEWS_STATIC_DIR", "/app/static"))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    await init_db(engine)
    app.state.sf = make_session_factory(engine)
    loop = IngestLoop(app.state.sf,
                      os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092"),
                      channel=os.environ.get("NEWS_CHANNEL", "news"),
                      report_writer=write_daily_report)
    task = asyncio.create_task(loop.run_forever())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await engine.dispose()


app = FastAPI(title="news app", lifespan=lifespan)
app.include_router(router)

if STATIC_DIR.is_dir():
    # The built frontend (vite base /apps/news/). html=True serves index.html
    # for the SPA routes.
    app.mount("/apps/news", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
