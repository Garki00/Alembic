# tools/weather.py
"""和风天气实时查询工具。

凭据不再从进程环境全局读取，而是由注册表按配置注入（见 :func:`build_weather_tools`）；
只有配置为空时才回落到 ``.env`` 的同名变量，保证命令行与测试能直接跑。
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

load_dotenv()

logger = logging.getLogger(__name__)

_DEFAULT_API_HOST = "na44ucu9jw.re.qweatherapi.com"


def _setting(config: dict, key: str, default: str = "") -> str:
    """读取一项配置：工具配置 → 环境变量 → 默认值。"""
    for value in ((config or {}).get(key), os.getenv(key), default):
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _build_token(config: dict) -> str:
    """生成和风天气 JWT 令牌；缺凭据时抛 RuntimeError。"""
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
        raise RuntimeError("缺少凭据：" + "、".join(missing))

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
    """主接口失败时尝试旧版接口（部分账号仍可用）。"""
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
    """查询过程本体；所有失败都返回 ``Error:`` 字符串而不是抛异常。"""
    if not city or not city.strip():
        return tool_error("empty_city", "城市名不能为空。", hint="请提供有效的城市名称，例如 '北京'。")
    city = city.strip()

    try:
        token = _build_token(config)
    except Exception as exc:
        # 缺凭据属于配置问题而不是程序异常，用 warning 以免日志里出现完整栈
        logger.warning("生成和风天气 JWT 失败：%s", exc)
        return tool_error(
            "auth_config",
            f"天气服务认证配置异常（{type(exc).__name__}: {exc}）。",
            hint="请在设置 → 工具里填写 QWEATHER_KEY_ID / PROJECT_ID / DEVELOPER_ID / PRIVATE_KEY。",
        )

    headers = {"Authorization": f"Bearer {token}"}
    api_host = _setting(config, "QWEATHER_API_HOST", _DEFAULT_API_HOST)

    # ---- 1. 城市名 → 经纬度 ----
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
                f"地理编码 API 返回错误码 {geo_data.get('code')}，无法解析城市 {city!r}。",
                hint="请确认城市名称是否正确。",
            )
        locations = geo_data.get("location") or []
        if not locations:
            return tool_error(
                "city_not_found",
                f"未找到城市 {city!r}。",
                hint="请提供更明确的名称，例如 '北京'、'上海市'、'Guangzhou'。",
            )
        lat = locations[0].get("lat")
        lon = locations[0].get("lon")
        if not lat or not lon:
            return tool_error("geo_missing", f"城市 {city!r} 的经纬度数据缺失，无法继续查询天气。")
    except requests.Timeout:
        return tool_error("geo_timeout", f"查询城市 {city!r} 的地理信息超时。", hint="请稍后重试。")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        return tool_error("geo_http", f"地理编码请求失败（HTTP {status}）：{exc}。")
    except requests.RequestException as exc:
        return tool_error("geo_network", f"地理编码网络请求异常：{exc}。")
    except ValueError:
        return tool_error("geo_json", "地理编码 API 返回了非 JSON 响应。", hint="请稍后重试。")

    # ---- 2. 实时天气（含降级） ----
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
                f"天气 API 返回业务错误码 {data.get('code')}，城市 {city!r} 的实时天气暂时不可用。",
            )
        return weather_resp.text

    except requests.Timeout:
        fallback = _fallback_weather_by_location(api_host, city, headers)
        if fallback:
            return fallback
        return tool_error("weather_timeout", f"查询城市 {city!r} 的实时天气超时。", hint="请稍后重试。")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        fallback = _fallback_weather_by_location(api_host, city, headers)
        if fallback:
            return fallback
        return tool_error(
            "weather_http",
            f"天气 API 请求失败（HTTP {status}），城市 {city!r} 的天气暂时无法获取。",
        )
    except requests.RequestException as exc:
        return tool_error("weather_network", f"天气网络请求异常：{exc}。")
    except ValueError:
        return tool_error("weather_json", "天气 API 返回了非 JSON 响应。", hint="请稍后重试。")


def build_weather_tools(config: dict) -> list:
    """按注入的凭据构造 ``get_weather`` 工具。"""

    @tool
    def get_weather(city: str) -> str:
        """查询某个城市的实时天气（和风天气 API）。

        Args:
            city: 城市名称，例如 "北京"、"上海"、"Guangzhou"。

        Returns:
            成功时返回天气 JSON 文本；失败时返回以 "Error:" 开头的说明。
        """
        return _fetch_weather(city, config)

    return [get_weather]
