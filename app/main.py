import logging
import time
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic_settings import BaseSettings
from prometheus_fastapi_instrumentator import Instrumentator

class Settings(BaseSettings):
    APP_NAME: str = "WeatherMonitor"
    LATITUDE: float = 55.7588 
    LONGITUDE: float = 37.62817
    WEATHER_API_URL: str = "https://api.open-meteo.com/v1/forecast"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    class Config:
        env_file = ".env"

settings = Settings()

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")

# --- Async HTTP Client Lifecycle ---
http_client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    logger.info("Starting up application and initializing HTTP client...")
    http_client = httpx.AsyncClient(timeout=10.0)
    yield
    logger.info("Shutting down application and closing HTTP client...")
    await http_client.aclose()

app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)


instrumentator = Instrumentator().instrument(app).expose(app)

# --- Services ---
async def fetch_weather(lat: float, lon: float) -> Dict[str, Any]:
    """
    Получает текущую погоду из внешнего API.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m",
        "hourly": "temperature_2m",
        "forecast_days": 1
    }
    
    try:
        response = await http_client.get(settings.WEATHER_API_URL, params=params)
        response.raise_for_status()
        data = response.json()
        
        if "current" not in data or "temperature_2m" not in data["current"]:
            raise ValueError("Invalid API response structure")
            
        return {
            "temperature": data["current"]["temperature_2m"],
            "unit": data["current_units"]["temperature_2m"],
            "city_coords": f"{lat}, {lon}"
        }
    except httpx.HTTPError as e:
        logger.error(f"HTTP error occurred while fetching weather: {e}")
        raise HTTPException(status_code=503, detail="Weather service unavailable")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """
    Главная страница: отображает температуру.
    """
    start_time = time.time()
    try:
        weather_data = await fetch_weather(settings.LATITUDE, settings.LONGITUDE)
        render_time = time.time() - start_time
        
        return templates.TemplateResponse(
            "index.html", 
            {
                "request": request, 
                "temperature": weather_data["temperature"],
                "unit": weather_data["unit"],
                "location": weather_data["city_coords"],
                "render_time": f"{render_time:.4f}"
            }
        )
    except HTTPException as e:
        return templates.TemplateResponse(
            "index.html", 
            {"request": request, "error": e.detail},
            status_code=e.status_code
        )

@app.get("/health")
async def health_check():
    """
    Легковесный эндпоинт для проверки доступности (liveness probe).
    """
    return {"status": "ok", "service": settings.APP_NAME}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)