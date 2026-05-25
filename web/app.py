"""FastAPI app for zipsa web UI.

API-first: every meaningful endpoint lives under `/api/*` and returns
JSON. The `/` route renders a thin Jinja shell whose Alpine.js fetches
those endpoints. This keeps the door open to swap the UI to a React/SPA
later without touching the backend.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from api.runs import router as runs_router
from api.skills import router as skills_router


_WEB_DIR = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_WEB_DIR / "ui" / "templates"))


app = FastAPI(title="zipsa-web", description="Web UI for zipsa")
app.include_router(skills_router)
app.include_router(runs_router)
app.mount(
    "/static",
    StaticFiles(directory=str(_WEB_DIR / "ui" / "static")),
    name="static",
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    # Starlette >= 0.26 prefers (request, name, context) over the legacy
    # (name, {"request": request, ...}) which now breaks Jinja's cache.
    return _templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    # Silence the browser's default favicon request — spike has no icon yet.
    return Response(status_code=204)
