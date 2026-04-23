"""Doctrans - Document Translation Platform

FastAPI application entry point.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import TOKEN_EXPIRE_DAYS
from .database import init_db
from .api.tasks import router as tasks_router, run_translation
from .api.glossaries import router as glossaries_router
from .api.languages import router as languages_router
from .api.settings import router as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Doctrans", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Token middleware — runs before routes, sets cookie on first visit
@app.middleware("http")
async def token_middleware(request: Request, call_next):
    token = request.cookies.get("token")

    # If no token, generate one and inject into request cookies
    # so routes can read it via _get_token()
    if not token:
        token = str(uuid.uuid4())

    # Inject into request state so routes can access it
    request.state.token = token

    response = await call_next(request)

    # Always ensure cookie is set (first visit or expired)
    if not request.cookies.get("token"):
        response.set_cookie(
            key="token",
            value=token,
            max_age=TOKEN_EXPIRE_DAYS * 86400,
            httponly=False,
        )

    return response


# Register routers
app.include_router(tasks_router)
app.include_router(glossaries_router)
app.include_router(languages_router)
app.include_router(settings_router)


# Serve frontend static files (production)
import os
frontend_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
