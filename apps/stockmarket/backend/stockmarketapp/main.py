"""Stockmarket app entrypoint: FastAPI serving the browse API + the built
frontend, with the Kafka ingest loop running alongside."""
import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from stockmarketapp.api import router
from stockmarketapp.db import (init_db, make_engine, make_session_factory,
                               seed_indexes)
from stockmarketapp.ingest import BacktestIngestLoop, IngestLoop

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(os.environ.get("STOCKMARKET_STATIC_DIR", "/app/static"))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    await init_db(engine)
    app.state.sf = make_session_factory(engine)
    # The three indexes are tracked from first boot, so the loader agent has
    # something to sync before anyone has opened the page.
    await seed_indexes(app.state.sf)
    bootstrap = os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092")
    loop = IngestLoop(app.state.sf, bootstrap)
    backtest_loop = BacktestIngestLoop(app.state.sf, bootstrap)
    tasks = [asyncio.create_task(loop.run_forever()),
            asyncio.create_task(backtest_loop.run_forever())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await engine.dispose()


app = FastAPI(title="stockmarket app", lifespan=lifespan)
app.include_router(router)

if STATIC_DIR.is_dir():
    # The built frontend (vite base /apps/stockmarket/). html=True serves
    # index.html for the SPA routes.
    app.mount("/apps/stockmarket",
              StaticFiles(directory=STATIC_DIR, html=True), name="ui")
