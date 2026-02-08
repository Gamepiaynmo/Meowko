"""Tests for weather utility functions."""

from src.providers.weather import weather_code_to_description


class TestWeatherCodeToDescription:
    def test_clear_sky(self):
        assert weather_code_to_description("0") == "晴"

    def test_cloudy(self):
        assert weather_code_to_description("3") == "阴"

    def test_rain(self):
        assert weather_code_to_description("61") == "小雨"
        assert weather_code_to_description("63") == "中雨"
        assert weather_code_to_description("65") == "大雨"

    def test_snow(self):
        assert weather_code_to_description("71") == "小雪"

    def test_thunderstorm(self):
        assert weather_code_to_description("95") == "雷暴"

    def test_unknown_code(self):
        assert weather_code_to_description("999") == "未知"

    def test_fog(self):
        assert weather_code_to_description("45") == "雾"
