# tools/weather.py
"""QWeather real-time weather lookup tool.

Credentials come from the tool configuration injected by the registry (see
:func:`build_weather_tools`); when a configuration value is empty, the same-named
``.env`` variable is used as a fallback.
"""

from __future__ import annotations

import logging
import os
import time as _time

import jwt
import requests
from dotenv import load_dotenv
from langchain.tools import tool

from tools.errors import tool_error
from tools.spec import ToolField, ToolSpec

load_dotenv()

logger = logging.getLogger(__name__)



def _setting(config: dict, key: str, default: str = "") -> str:
    """Read one setting: tool configuration → environment variable → default."""
    for value in ((config or {}).get(key), os.getenv(key), default):
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _build_token(config: dict) -> str:
    """Build a QWeather JWT token; raises RuntimeError when credentials are missing."""
    key_id = _setting(config, "QWEATHER_KEY_ID")
    project_id = _setting(config, "QWEATHER_PROJECT_ID")
    developer_id = _setting(config, "QWEATHER_DEVELOPER_ID")
    private_key = _setting(config, "QWEATHER_PRIVATE_KEY")

    missing = [
        name
        for name, value in (
            ("QWEATHER_KEY_ID", key_id),
            ("QWEATHER_PROJECT_ID", project_id),
            ("QWEATHER_DEVELOPER_ID", developer_id),
            ("QWEATHER_PRIVATE_KEY", private_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Missing credentials: " + ", ".join(missing))

    private_key = private_key.replace("\\n", "\n")
    return jwt.encode(
        {
            "iss": developer_id,
            "sub": project_id,
            "iat": int(_time.time()) - 30,
            "exp": int(_time.time()) + 3600,
        },
        private_key,
        algorithm="EdDSA",
        headers={"alg": "EdDSA", "kid": key_id},
    )


def _fallback_weather_by_location(api_host: str, city: str, headers: dict) -> str | None:
    """Try the legacy endpoint when the primary one fails (still available for some accounts)."""
    try:
        resp = requests.get(
            f"https://{api_host}/v7/weather/now",
            params={"location": city},
            headers=headers,
            timeout=8,
        )
        if resp.ok:
            return resp.text
    except requests.RequestException as exc:
        logger.warning("备用天气接口也失败：%s", exc)
    return None


def _fetch_weather(city: str, config: dict) -> str:
    """Lookup implementation; every failure returns an ``Error:`` string instead of raising."""
    if not city or not city.strip():
        return tool_error("empty_city", "The city name must not be empty.", hint="Provide a valid city name, for example 'Beijing'.")
    city = city.strip()

    try:
        token = _build_token(config)
    except Exception as exc:
        # Missing credentials are a configuration problem, not a program failure;
        # log at warning level so the full stack does not appear in the log.
        logger.warning("生成和风天气 JWT 失败：%s", exc)
        return tool_error(
            "auth_config",
            f"Weather service authentication is misconfigured ({type(exc).__name__}: {exc}).",
            hint="Enter QWEATHER_KEY_ID / PROJECT_ID / DEVELOPER_ID / PRIVATE_KEY under Settings → Tools.",
        )

    headers = {"Authorization": f"Bearer {token}"}
    api_host = _setting(config, "QWEATHER_API_HOST")
    if not api_host:
        return tool_error(
            "missing_api_host",
            "QWEATHER_API_HOST is missing, so the QWeather API cannot be reached.",
            hint='Enter your API Host (without https://) under Settings → Tools → weather tool.',
        )

    # ---- 1. City name → latitude/longitude ----
    try:
        geo_resp = requests.get(
            f"https://{api_host}/geo/v2/city/lookup",
            params={"location": city, "number": 1},
            headers=headers,
            timeout=10,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()

        if geo_data.get("code") != "200":
            return tool_error(
                "geo_code",
                f"The geocoding API returned error code {geo_data.get('code')}; cannot resolve city {city!r}.",
                hint="Check that the city name is correct.",
            )
        locations = geo_data.get("location") or []
        if not locations:
            return tool_error(
                "city_not_found",
                f"City {city!r} was not found.",
                hint="Provide a more specific name, for example 'Beijing', 'Shanghai', or 'Guangzhou'.",
            )
        lat = locations[0].get("lat")
        lon = locations[0].get("lon")
        if not lat or not lon:
            return tool_error("geo_missing", f"Latitude/longitude data for city {city!r} is missing; cannot continue the weather lookup.")
    except requests.Timeout:
        return tool_error("geo_timeout", f"Timed out looking up geographic information for city {city!r}.", hint="Retry later.")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return tool_error("geo_http", f"Geocoding request failed (HTTP {status}): {exc}.")
    except requests.RequestException as exc:
        return tool_error("geo_network", f"Geocoding network request failed: {exc}.")
    except ValueError:
        return tool_error("geo_json", "The geocoding API returned a non-JSON response.", hint="Retry later.")

    # ---- 2. Current weather (with fallback) ----
    try:
        weather_resp = requests.get(
            f"https://{api_host}/weather/v1/current/{lat}/{lon}",
            headers=headers,
            timeout=10,
        )
        weather_resp.raise_for_status()
        data = weather_resp.json()

        if isinstance(data, dict) and data.get("code") not in (None, "200"):
            return tool_error(
                "weather_code",
                f"The weather API returned business error code {data.get('code')}; current weather for city {city!r} is temporarily unavailable.",
            )
        return weather_resp.text

    except requests.Timeout:
        fallback = _fallback_weather_by_location(api_host, city, headers)
        if fallback:
            return fallback
        return tool_error("weather_timeout", f"Timed out fetching current weather for city {city!r}.", hint="Retry later.")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        fallback = _fallback_weather_by_location(api_host, city, headers)
        if fallback:
            return fallback
        return tool_error(
            "weather_http",
            f"The weather API request failed (HTTP {status}); weather for city {city!r} is temporarily unavailable.",
        )
    except requests.RequestException as exc:
        return tool_error("weather_network", f"Network request for weather failed: {exc}.")
    except ValueError:
        return tool_error("weather_json", "The weather API returned a non-JSON response.", hint="Retry later.")


def build_weather_tools(config: dict) -> list:
    """Build the ``get_weather`` tool from the injected credentials."""

    @tool
    def get_weather(city: str) -> str:
        """Get the current weather for a city (QWeather API).

        Args:
            city: City name, for example "Beijing", "Shanghai", or "Guangzhou".

        Returns:
            Weather JSON text on success; a message starting with "Error:" on failure.
        """
        return _fetch_weather(city, config)

    return [get_weather]


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_weather",
        label="实时天气",
        description="查询城市实时天气，需和风天气凭据",
        fields=(
            ToolField("QWEATHER_KEY_ID", "Key ID", secret=True),
            ToolField("QWEATHER_PROJECT_ID", "Project ID"),
            ToolField("QWEATHER_DEVELOPER_ID", "Developer ID"),
            ToolField("QWEATHER_PRIVATE_KEY", "Private Key（Ed25519）", secret=True, multiline=True),
            ToolField(
                "QWEATHER_API_HOST",
                "API Host",
                placeholder="填写你的和风天气 API Host（不含 https://）",
            ),
        ),
        tool_names=("get_weather",),
        default_enabled=False,
        builder=lambda config, _ctx: build_weather_tools(dict(config)),
        defer=True,
        search_hint="weather forecast temperature 天气 气温 预报",
    ),
)

