"""Weather provider using Open-Meteo API."""

import aiohttp

from src.config import get_config

_session: aiohttp.ClientSession | None = None


async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


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

    session = await _get_session()
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
        "0": "晴",
        "1": "大部晴朗",
        "2": "多云",
        "3": "阴",
        "45": "雾",
        "48": "雾凇",
        "51": "小毛毛雨",
        "53": "中毛毛雨",
        "55": "大毛毛雨",
        "56": "冻毛毛雨",
        "57": "强冻毛毛雨",
        "61": "小雨",
        "63": "中雨",
        "65": "大雨",
        "66": "小冻雨",
        "67": "大冻雨",
        "71": "小雪",
        "73": "中雪",
        "75": "大雪",
        "77": "霰",
        "80": "小阵雨",
        "81": "中阵雨",
        "82": "大阵雨",
        "85": "小阵雪",
        "86": "大阵雪",
        "95": "雷暴",
        "96": "雷暴伴小冰雹",
        "99": "雷暴伴大冰雹",
    }
    return codes.get(code, "未知")
