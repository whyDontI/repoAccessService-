from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if db._pool is not None:
        await db._pool.close()


app = FastAPI(title="Repository Access Service", lifespan=lifespan)
app.include_router(router)
