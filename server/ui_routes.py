import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import RedirectResponse

from server.tunnel import get_tunnel_url


router = APIRouter()
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "web-ui")


def _read_html(filename: str) -> HTMLResponse:
    path = os.path.join(STATIC_DIR, filename)
    if not os.path.exists(path):
        return HTMLResponse(
            content=f"<html><body><h1>{filename} not found</h1></body></html>",
            status_code=404,
        )

    with open(path, "r", encoding="utf-8") as html_file:
        return HTMLResponse(content=html_file.read(), status_code=200)


def _redirect(target: str) -> RedirectResponse:
    return RedirectResponse(url=target, status_code=307)


def _redirect_with_query(request: Request, target: str) -> RedirectResponse:
    query_string = request.url.query
    if query_string:
        return _redirect(f"{target}?{query_string}")
    return _redirect(target)


@router.get("/", response_class=HTMLResponse)
async def landing_page():
    return _read_html("landing.html")


@router.get("/landing", response_class=HTMLResponse)
async def landing_page_alias():
    return _read_html("landing.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return _read_html("login.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return _read_html("index.html")


@router.get("/terminal", response_class=HTMLResponse)
async def terminal_page():
    return _read_html("terminal.html")


@router.get("/shell", response_class=HTMLResponse)
async def bash_terminal_page():
    return _read_html("bash.html")


@router.get("/login.html", response_class=RedirectResponse)
async def login_page_legacy(request: Request):
    return _redirect_with_query(request, "/login")


@router.get("/index.html", response_class=RedirectResponse)
async def dashboard_page_legacy(request: Request):
    return _redirect_with_query(request, "/dashboard")


@router.get("/terminal.html", response_class=RedirectResponse)
async def terminal_page_legacy(request: Request):
    return _redirect_with_query(request, "/terminal")


@router.get("/bash.html", response_class=RedirectResponse)
async def bash_terminal_page_legacy(request: Request):
    return _redirect_with_query(request, "/shell")


@router.get("/css/{filename}")
async def get_css(filename: str):
    css_dir = Path(STATIC_DIR, "css").resolve()
    css_path = css_dir.joinpath(filename).resolve()
    if not css_path.is_relative_to(css_dir) or not css_path.is_file():
        raise HTTPException(status_code=404, detail="CSS file not found")
    return FileResponse(str(css_path), media_type="text/css")


@router.get("/js/{filename}")
async def get_js(filename: str):
    js_dir = Path(STATIC_DIR, "js").resolve()
    js_path = js_dir.joinpath(filename).resolve()
    if not js_path.is_relative_to(js_dir) or not js_path.is_file():
        raise HTTPException(status_code=404, detail="JS file not found")
    return FileResponse(str(js_path), media_type="application/javascript")


@router.get("/api/ui/tunnel-url")
async def get_ui_tunnel_url():
    url = await get_tunnel_url()
    return {"url": url}
