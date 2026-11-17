"""Сервер TripWise: аккаунты, проверка доступности API и интерфейс."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.accounts import current_user, init_database, router

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

@asynccontextmanager
async def lifespan(app):
    init_database()
    yield


app = FastAPI(
    title="TripWise API",
    description="Аккаунты TripWise. Генерация и сохранение маршрутов пока не подключены.",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/") or request.url.path == "/trips":
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    # Стандартные ошибки могут содержать input с паролем. Возвращаем только текст.
    return JSONResponse(status_code=422, content={"detail": "Проверьте почту и пароль. При регистрации: от 8 до 128 символов и одинаковый повтор пароля."}, headers={"Cache-Control": "no-store"})


@app.get("/api/health", tags=["Состояние сервера"])
def health() -> dict[str, str]:
    """Подтверждает, что приложение запущено; не проверяет будущую БД или AI."""
    return {"status": "ok", "service": "TripWise"}


@app.get("/trips", include_in_schema=False)
def trips_page(request: Request):
    try:
        current_user(request)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        return RedirectResponse("/", status_code=303)
    return FileResponse(PROJECT_ROOT / "backend" / "pages" / "trips.html", headers={"Cache-Control": "no-store"})


# API объявляем первым: обработчики проверяются по порядку.
# Браузеру доступны только файлы frontend, не весь проект и не будущая база.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
