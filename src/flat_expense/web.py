"""SSH-only browser UI. SSH is the access boundary; service credentials stay server-side."""

import hashlib
import hmac
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from flat_expense import service as s

COOKIE = "flat_web_session"
LIFETIME = 8 * 60 * 60
ASSETS = Path(__file__).with_name("static")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


def local_request(request, config):
    if not config.web_enabled:
        raise HTTPException(401, "Веб-интерфейс не включён.")
    # Protect against DNS rebinding; never trust forwarded Host headers here.
    if request.url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "Откройте интерфейс через локальный SSH-туннель.")


def signature(payload, config):
    return hmac.new(
        config.api_token.get_secret_value().encode(),
        ("flat-web-v1:" + payload).encode(),
        hashlib.sha256,
    ).hexdigest()


def session_cookie(config):
    payload = f"{config.web_owner_id}:{int(time.time()) + LIFETIME}:{secrets.token_hex(16)}"
    return payload + ":" + signature(payload, config)


def browser_actor(request, config):
    local_request(request, config)
    token = request.cookies.get(COOKIE, "")
    try:
        if len(token) > 200:
            raise ValueError
        owner, expiry, nonce, mac = token.split(":")
        payload = f"{owner}:{expiry}:{nonce}"
        if (
            not hmac.compare_digest(mac, signature(payload, config))
            or int(expiry) <= time.time()
            or int(owner) != config.web_owner_id
            or int(owner) not in config.allowed_ids
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(401, "Сессия истекла. Обновите страницу.") from None
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        expected_origin = str(request.base_url).rstrip("/")
        if (
            request.headers.get("origin") != expected_origin
            or request.headers.get("x-flat-expense") != "web"
        ):
            raise HTTPException(403, "Запрос должен быть отправлен из интерфейса приложения.")
    return int(owner)


def install_web(app, config):
    @app.middleware("http")
    async def browser_headers(request, call_next):
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers[name] = value
        return response

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        local_request(request, config)
        response = FileResponse(ASSETS / "index.html", headers=HEADERS)
        response.set_cookie(
            COOKIE,
            session_cookie(config),
            max_age=LIFETIME,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    @app.get("/assets/{name}", include_in_schema=False)
    def asset(name: str, request: Request):
        local_request(request, config)
        if name not in ("app.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(ASSETS / name, headers=HEADERS)

    @app.get("/web/bootstrap", include_in_schema=False)
    def bootstrap(request: Request):
        browser_actor(request, config)
        return JSONResponse(
            {"today": s.today().isoformat(), "move_month": "2027-08"}, headers=HEADERS
        )
