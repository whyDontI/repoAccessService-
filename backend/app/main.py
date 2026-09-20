from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if db._pool is not None:
        await db._pool.close()


app = FastAPI(title="Repository Access Service", lifespan=lifespan)
# Authn/authz is explicitly out of scope (see README) and this never
# leaves the local compose stack, so a permissive CORS policy is fine --
# the frontend just needs to be able to call this from its own origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
