"""Doctrans - Document Translation Platform

FastAPI application entry point.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import JwtAuthenticationError, authenticate_authorization_header, validate_jwt_configuration
from .config import CORS_ALLOWED_ORIGINS
from .database import init_db
from .api.tasks import router as tasks_router, run_translation
from .api.glossaries import router as glossaries_router
from .api.languages import router as languages_router
from .api.settings import router as settings_router
from .api.ws import ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_jwt_configuration()
    await init_db()
    yield


app = FastAPI(title="Doctrans", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def jwt_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method != "OPTIONS":
        try:
            request.state.user = authenticate_authorization_header(request.headers.get("Authorization"))
        except JwtAuthenticationError as exc:
            return JSONResponse(status_code=401, content={"detail": str(exc)})
    return await call_next(request)


# Register routers
app.include_router(tasks_router)
app.include_router(glossaries_router)
app.include_router(languages_router)
app.include_router(settings_router)
app.include_router(ws_router)


# Serve frontend static files (production)
import os

_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath("app/main.py")))
_frontend_dist_candidates = [
    os.path.join(_backend_dir, "frontend", "dist"),
    os.path.join(os.path.dirname(_backend_dir), "frontend", "dist"),
]

for frontend_dist in _frontend_dist_candidates:
    if os.path.isdir(frontend_dist):
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
        break
