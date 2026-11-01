"""Password-protected browser UI; service credentials stay server-side."""

import hashlib
import hmac
import secrets
import threading
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

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
LOGIN_WINDOW = 5 * 60
LOGIN_LIMIT = 6


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt" or (int(n), int(r), int(p)) != (16384, 8, 1):
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def allowed_request(request, config):
    if not config.web_enabled:
        raise HTTPException(401, "Веб-интерфейс не включён.")
    if (request.url.hostname or "").lower() not in config.allowed_web_hosts:
        raise HTTPException(403, "Этот адрес не разрешён для веб-интерфейса.")


def signature(payload, config):
    return hmac.new(
        config.api_token.get_secret_value().encode(),
        ("flat-web-v1:" + payload).encode(),
        hashlib.sha256,
    ).hexdigest()


def session_cookie(config):
    payload = f"{config.web_owner_id}:{int(time.time()) + LIFETIME}:{secrets.token_hex(16)}"
    return payload + ":" + signature(payload, config)


def session_owner(request, config):
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
        return int(owner)
    except (ValueError, TypeError):
        return None


def browser_actor(request, config):
    allowed_request(request, config)
    owner = session_owner(request, config)
    if owner is None:
        raise HTTPException(401, "Сессия истекла. Войдите снова.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        expected_origin = str(request.base_url).rstrip("/")
        if (
            request.headers.get("origin") != expected_origin
            or request.headers.get("x-flat-expense") != "web"
        ):
            raise HTTPException(403, "Запрос должен быть отправлен из интерфейса приложения.")
    return owner


def install_web(app, config):
    attempts: dict[str, list[float]] = {}
    attempts_lock = threading.Lock()

    @app.middleware("http")
    async def browser_headers(request, call_next):
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers[name] = value
        return response

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        allowed_request(request, config)
        if config.web_public and session_owner(request, config) is None:
            return RedirectResponse("/login", status_code=303, headers=HEADERS)
        response = FileResponse(ASSETS / "index.html", headers=HEADERS)
        if not config.web_public and session_owner(request, config) is None:
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

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        allowed_request(request, config)
        if not config.web_public or session_owner(request, config) is not None:
            return RedirectResponse("/", status_code=303, headers=HEADERS)
        return FileResponse(ASSETS / "login.html", headers=HEADERS)

    @app.post("/web/login", include_in_schema=False)
    def login(data: LoginIn, request: Request):
        allowed_request(request, config)
        if not config.web_public or config.web_password_hash is None:
            raise HTTPException(404)
        expected_origin = str(request.base_url).rstrip("/")
        if (
            request.headers.get("origin") != expected_origin
            or request.headers.get("x-flat-expense") != "login"
        ):
            raise HTTPException(403, "Запрос должен быть отправлен со страницы входа.")
        address = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with attempts_lock:
            recent = [stamp for stamp in attempts.get(address, []) if now - stamp < LOGIN_WINDOW]
            attempts[address] = recent
            if len(recent) >= LOGIN_LIMIT:
                raise HTTPException(429, "Слишком много попыток. Повторите позже.")
        encoded = config.web_password_hash.get_secret_value()
        if not password_matches(data.password, encoded):
            with attempts_lock:
                attempts[address].append(now)
            raise HTTPException(401, "Неверный пароль.")
        with attempts_lock:
            attempts.pop(address, None)
        response = JSONResponse({"status": "ok"}, headers=HEADERS)
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

    @app.post("/web/logout", include_in_schema=False)
    def logout(request: Request):
        browser_actor(request, config)
        response = JSONResponse({"status": "ok"}, headers=HEADERS)
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/assets/{name}", include_in_schema=False)
    def asset(name: str, request: Request):
        allowed_request(request, config)
        if name not in ("app.js", "login.js", "style.css"):
            raise HTTPException(404)
        return FileResponse(ASSETS / name, headers=HEADERS)

    @app.get("/web/bootstrap", include_in_schema=False)
    def bootstrap(request: Request):
        browser_actor(request, config)
        return JSONResponse(
            {"today": s.today().isoformat(), "move_month": "2027-08"}, headers=HEADERS
        )
