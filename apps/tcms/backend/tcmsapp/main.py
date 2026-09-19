"""TCMS app entrypoint: FastAPI serving the browse API + the built frontend,
with the reconciler (announce + retention) running alongside."""
import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tcmsapp.api import router
from tcmsapp.db import init_db, make_engine, make_session_factory
from tcmsapp.reconcile import DEFAULT_RETENTION_DAYS, Reconciler, make_api_client

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(os.environ.get("TCMS_STATIC_DIR", "/app/static"))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    engine = make_engine()
    await init_db(engine)
    app.state.sf = make_session_factory(engine)
    api = make_api_client()
    rec = Reconciler(app.state.sf, None, api,
                     channel=os.environ.get("TCMS_CHANNEL", "qa"),
                     retention_days=int(os.environ.get("TCMS_RETENTION_DAYS",
                                                       DEFAULT_RETENTION_DAYS)))
    task = asyncio.create_task(rec.run_forever(os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092")))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if api is not None:
            await api.aclose()
        await engine.dispose()


app = FastAPI(title="tcms app", lifespan=lifespan)
app.include_router(router)

if STATIC_DIR.is_dir():
    app.mount("/apps/tcms", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
