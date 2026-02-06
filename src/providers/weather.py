"""Weather provider using Open-Meteo API."""

import aiohttp

from src.config import get_config


async def get_weather() -> dict[str, str]:
    """Fetch current weather from Open-Meteo API.

    Returns:
        Dict with weather_code, temp_max, temp_min
    """
    config = get_config()
    weather_config = config.weather

    latitude = weather_config["latitude"]
    longitude = weather_config["longitude"]
    timezone = weather_config["timezone"]

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={latitude}&longitude={longitude}"
        f"&daily=weather_code,temperature_2m_max,temperature_2m_min"
        f"&timezone={timezone}&forecast_days=1"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()
            daily = data.get("daily", {})
            return {
                "weather_code": str(daily.get("weather_code", [""])[0]),
                "temp_max": str(daily.get("temperature_2m_max", [""])[0]),
                "temp_min": str(daily.get("temperature_2m_min", [""])[0]),
            }


def weather_code_to_description(code: str) -> str:
    """Convert WMO weather code to human-readable description."""
    codes = {
        "0": "Clear sky",
        "1": "Mainly clear",
        "2": "Partly cloudy",
        "3": "Overcast",
        "45": "Fog",
        "48": "Depositing rime fog",
        "51": "Light drizzle",
        "53": "Moderate drizzle",
        "55": "Dense drizzle",
        "56": "Light freezing drizzle",
        "57": "Dense freezing drizzle",
        "61": "Slight rain",
        "63": "Moderate rain",
        "65": "Heavy rain",
        "66": "Light freezing rain",
        "67": "Heavy freezing rain",
        "71": "Slight snow fall",
        "73": "Moderate snow fall",
        "75": "Heavy snow fall",
        "77": "Snow grains",
        "80": "Slight rain showers",
        "81": "Moderate rain showers",
        "82": "Violent rain showers",
        "85": "Slight snow showers",
        "86": "Heavy snow showers",
        "95": "Thunderstorm",
        "96": "Thunderstorm with slight hail",
        "99": "Thunderstorm with heavy hail",
    }
    return codes.get(code, "Unknown")
