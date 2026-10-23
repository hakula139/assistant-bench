"""The vote API and the render origin, both served on the loopback interface."""

import asyncio
from pathlib import Path, PurePath

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response
from starlette.types import Scope

from assistant_bench.arena.service import (
    OUTPUTS_PATH,
    Arena,
    FamilyView,
    NothingToUndo,
    SessionView,
    UnknownItem,
    UnlistedDefects,
)
from assistant_bench.arena.store import Choice, Defects


HOST = '127.0.0.1'
APP_PORT = 8790
RENDER_PORT = 8791
APP_ORIGIN = f'http://{HOST}:{APP_PORT}'
RENDER_ORIGIN = f'http://{HOST}:{RENDER_PORT}'

VOTE_DIR = 'vote'
VOTE_POLICY = (
    "default-src 'none'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self'; "
    f"connect-src 'self'; frame-src {RENDER_ORIGIN}; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)

# Outputs load by URL from a second origin, since a `srcdoc` frame inherits the
# vote page's policy. `sandbox` without `allow-same-origin` makes each opaque.
RENDER_POLICY = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data:; connect-src 'none'; "
    f'frame-ancestors {APP_ORIGIN}'
)


class StartSession(BaseModel):
    model_config = ConfigDict(extra='forbid')

    family: str


NOTE_MAX_CHARS = 500


class VoteRequest(BaseModel):
    """The owner's choice on a pair, where `a` is the left pane."""

    model_config = ConfigDict(extra='forbid')

    choice: Choice
    defects: Defects = Defects()
    note: str | None = Field(default=None, max_length=NOTE_MAX_CHARS)


def same_origin(request: Request) -> None:
    """Reject a write that the vote page itself did not send."""
    if request.headers.get('origin') != APP_ORIGIN:
        raise HTTPException(status_code=403, detail='cross-origin request')


class SiteFiles(StaticFiles):
    """The built site, where every file under `VOTE_DIR` carries `VOTE_POLICY`.

    `path` arrives normalized, so a doubled or percent-encoded slash still matches.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if PurePath(path).parts[:1] == (VOTE_DIR,):
            response.headers['Content-Security-Policy'] = VOTE_POLICY
        return response


def create_app(arena: Arena, site: Path | None = None) -> FastAPI:
    """Build the vote API under `/api`, serving the built site at `/`."""
    app = _app()
    app.add_exception_handler(NothingToUndo, _conflict)
    app.add_exception_handler(UnlistedDefects, _unprocessable)
    router = APIRouter(prefix='/api')
    writes = APIRouter(dependencies=[Depends(same_origin)])

    @router.get('/families')
    async def families() -> list[FamilyView]:
        """List the families with outputs to vote on."""
        return arena.families()

    @writes.post('/sessions', status_code=201)
    async def start(body: StartSession) -> SessionView:
        """Start a session on a family, with its first pair."""
        return arena.start(body.family)

    @writes.post('/sessions/{session_id}/pair')
    async def resume(session_id: str) -> SessionView:
        """Return the session's current pair, drawing one if none is open."""
        return arena.resume(session_id)

    @writes.post('/pairs/{token}/vote')
    async def vote(token: str, body: VoteRequest) -> SessionView:
        """Record a vote on a pair and return the session's next pair."""
        return arena.vote(token, body.choice, body.defects, body.note)

    @writes.post('/sessions/{session_id}/undo')
    async def undo(session_id: str) -> SessionView:
        """Withdraw the session's latest vote and show its pair again."""
        return arena.undo(session_id)

    router.include_router(writes)
    app.include_router(router)
    if site is not None:
        app.mount('/', SiteFiles(directory=site, html=True))
    return app


def create_render_app(arena: Arena) -> FastAPI:
    """Build the origin that serves each HTML output under its sandbox policy."""
    app = _app()

    @app.get(f'{OUTPUTS_PATH}/{{token}}')
    async def output(token: str) -> HTMLResponse:
        return HTMLResponse(
            arena.document(token), headers={'Content-Security-Policy': RENDER_POLICY}
        )

    return app


def serve(arena: Arena, site: Path) -> None:
    """Serve the vote page and API on `APP_PORT` and the outputs on `RENDER_PORT`."""
    servers = [
        uvicorn.Server(uvicorn.Config(app, host=HOST, port=port))
        for app, port in (
            (create_app(arena, site), APP_PORT),
            (create_render_app(arena), RENDER_PORT),
        )
    ]

    async def run() -> None:
        await asyncio.gather(*(server.serve() for server in servers))

    asyncio.run(run())


def _app() -> FastAPI:
    """Build an app on the loopback host that serves no API docs.

    FastAPI serves its docs pages only beside an OpenAPI document, and their page
    loads scripts from a CDN.
    """
    app = FastAPI(openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST])
    app.add_exception_handler(UnknownItem, _not_found)
    return app


async def _not_found(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({'detail': 'not found'}, status_code=404)


async def _conflict(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({'detail': 'no vote to undo'}, status_code=409)


async def _unprocessable(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({'detail': str(error)}, status_code=422)
