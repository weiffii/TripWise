"""Сервер TripWise: аккаунты, проверка доступности API и интерфейс."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.accounts import current_user, init_database, router
from backend.catalog import router as catalog_router
from backend.trips import router as trips_router
from backend.route_previews import router as preview_router
from backend.route_geometry import router as geometry_router
from backend.drafts import init_drafts, router as drafts_router
from backend.shanghai import init_routes, router as shanghai_router

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

@asynccontextmanager
async def lifespan(app):
    init_database()
    init_drafts()
    init_routes()
    yield


app = FastAPI(
    title="TripWise API",
    description="TripWise: Москва и Шанхай, предпросмотр маршрута и явное сохранение поездки.",
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(drafts_router)
app.include_router(shanghai_router)
app.include_router(catalog_router)
app.include_router(trips_router)
app.include_router(preview_router)
app.include_router(geometry_router)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth/", "/api/trip-draft", "/api/planned-trips", "/api/route-previews", "/api/trips")) or request.url.path in {"/trips", "/trip/new", "/trip/shanghai", "/trip/result"}:
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError):
    if request.url.path.startswith(('/api/planned-trips', '/api/route-previews')):
        return JSONResponse(status_code=422, content={'detail': 'Проверьте параметры пилотной поездки: город, даты, интересы, группу, время и бюджет.'})
    # Стандартные ошибки могут содержать input с паролем. Возвращаем только текст.
    if request.url.path == "/api/trip-draft" or request.url.path.startswith("/api/trips"):

        return JSONResponse(status_code=422, content={"detail": "Проверьте поля анкеты: время, количество людей, бюджет и выбранные варианты."}, headers={"Cache-Control": "no-store"})
    return JSONResponse(status_code=422, content={"detail": "Проверьте почту и пароль. При регистрации: от 8 до 128 символов и одинаковый повтор пароля."}, headers={"Cache-Control": "no-store"})


@app.get("/api/health", tags=["Состояние сервера"])
def health() -> dict[str, str]:
    """Подтверждает, что приложение запущено; не проверяет будущую БД или AI."""
    return {"status": "ok", "service": "TripWise"}


@app.get("/trips", include_in_schema=False)
def trips_page(request: Request):
    return protected_page(request, "trips.html")


@app.get("/trip/new", include_in_schema=False)
def questionnaire_page(request: Request):
    return protected_page(request, "questionnaire.html")


@app.get('/trip/result', include_in_schema=False)
def result_page(request: Request):
    return protected_page(request, 'result.html')


@app.get('/route-map.js', include_in_schema=False)
def route_map_script():
    return FileResponse(PROJECT_ROOT / 'ai_ds/web_demo/shanghai-map.js', media_type='text/javascript')


def protected_page(request: Request, filename: str):
    try:
        current_user(request)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        return RedirectResponse("/", status_code=303)
    return FileResponse(PROJECT_ROOT / "backend" / "pages" / filename, headers={"Cache-Control": "no-store"})


# API объявляем первым: обработчики проверяются по порядку.
# Браузеру доступны только файлы frontend, не весь проект и не будущая база.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
