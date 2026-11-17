# tools/time.py
"""Time tool: get the current time for the local machine or a specified region / city / time zone.

Four input forms are supported (case- and space-insensitive):

1. Local: ``local`` and equivalent Chinese aliases;
2. IANA time zone names: ``Asia/Shanghai``, ``UTC``, ``America/New_York``;
3. Chinese or English names of common cities / provinces / countries;
4. UTC offsets: ``UTC+8``, ``GMT-5``, ``+08:00``, and Chinese offset aliases.

Time zone data comes from the standard library :mod:`zoneinfo` IANA database when
available. Windows does not ship IANA data by default, so ``tzdata`` must be
installed in the virtual environment (already declared in pyproject). When that
data is unavailable, the built-in fixed-offset table is used and the result
explicitly notes that daylight saving time is not applied.
"""

from __future__ import annotations

import difflib
import re
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from functools import lru_cache

from langchain.tools import tool
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from tools.errors import tool_error

__all__ = ["get_current_time", "list_timezones"]

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# ---------------------------------------------------------------------------
# Region / city / country name → IANA time zone
# ---------------------------------------------------------------------------
_CITY_ALIASES: dict[str, str] = {
    # ---- China ----
    "中国": "Asia/Shanghai",
    "中国大陆": "Asia/Shanghai",
    "china": "Asia/Shanghai",
    "cn": "Asia/Shanghai",
    "北京": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "上海": "Asia/Shanghai",
    "shanghai": "Asia/Shanghai",
    "广州": "Asia/Shanghai",
    "guangzhou": "Asia/Shanghai",
    "深圳": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "东莞": "Asia/Shanghai",
    "dongguan": "Asia/Shanghai",
    "佛山": "Asia/Shanghai",
    "foshan": "Asia/Shanghai",
    "珠海": "Asia/Shanghai",
    "zhuhai": "Asia/Shanghai",
    "杭州": "Asia/Shanghai",
    "hangzhou": "Asia/Shanghai",
    "宁波": "Asia/Shanghai",
    "ningbo": "Asia/Shanghai",
    "温州": "Asia/Shanghai",
    "wenzhou": "Asia/Shanghai",
    "南京": "Asia/Shanghai",
    "nanjing": "Asia/Shanghai",
    "苏州": "Asia/Shanghai",
    "suzhou": "Asia/Shanghai",
    "无锡": "Asia/Shanghai",
    "wuxi": "Asia/Shanghai",
    "常州": "Asia/Shanghai",
    "changzhou": "Asia/Shanghai",
    "天津": "Asia/Shanghai",
    "tianjin": "Asia/Shanghai",
    "重庆": "Asia/Shanghai",
    "chongqing": "Asia/Shanghai",
    "成都": "Asia/Shanghai",
    "chengdu": "Asia/Shanghai",
    "武汉": "Asia/Shanghai",
    "wuhan": "Asia/Shanghai",
    "西安": "Asia/Shanghai",
    "xian": "Asia/Shanghai",
    "长沙": "Asia/Shanghai",
    "changsha": "Asia/Shanghai",
    "郑州": "Asia/Shanghai",
    "zhengzhou": "Asia/Shanghai",
    "青岛": "Asia/Shanghai",
    "qingdao": "Asia/Shanghai",
    "济南": "Asia/Shanghai",
    "jinan": "Asia/Shanghai",
    "合肥": "Asia/Shanghai",
    "hefei": "Asia/Shanghai",
    "福州": "Asia/Shanghai",
    "fuzhou": "Asia/Shanghai",
    "厦门": "Asia/Shanghai",
    "xiamen": "Asia/Shanghai",
    "泉州": "Asia/Shanghai",
    "quanzhou": "Asia/Shanghai",
    "昆明": "Asia/Shanghai",
    "kunming": "Asia/Shanghai",
    "南宁": "Asia/Shanghai",
    "nanning": "Asia/Shanghai",
    "海口": "Asia/Shanghai",
    "haikou": "Asia/Shanghai",
    "三亚": "Asia/Shanghai",
    "sanya": "Asia/Shanghai",
    "贵阳": "Asia/Shanghai",
    "guiyang": "Asia/Shanghai",
    "兰州": "Asia/Shanghai",
    "lanzhou": "Asia/Shanghai",
    "西宁": "Asia/Shanghai",
    "xining": "Asia/Shanghai",
    "银川": "Asia/Shanghai",
    "yinchuan": "Asia/Shanghai",
    "呼和浩特": "Asia/Shanghai",
    "hohhot": "Asia/Shanghai",
    "石家庄": "Asia/Shanghai",
    "shijiazhuang": "Asia/Shanghai",
    "太原": "Asia/Shanghai",
    "taiyuan": "Asia/Shanghai",
    "沈阳": "Asia/Shanghai",
    "shenyang": "Asia/Shanghai",
    "大连": "Asia/Shanghai",
    "dalian": "Asia/Shanghai",
    "长春": "Asia/Shanghai",
    "changchun": "Asia/Shanghai",
    "哈尔滨": "Asia/Shanghai",
    "harbin": "Asia/Shanghai",
    "南昌": "Asia/Shanghai",
    "nanchang": "Asia/Shanghai",
    "拉萨": "Asia/Shanghai",
    "lhasa": "Asia/Shanghai",
    # Provincial-level regions (all use Beijing time, UTC+8)
    "广东": "Asia/Shanghai",
    "guangdong": "Asia/Shanghai",
    "江苏": "Asia/Shanghai",
    "jiangsu": "Asia/Shanghai",
    "浙江": "Asia/Shanghai",
    "zhejiang": "Asia/Shanghai",
    "四川": "Asia/Shanghai",
    "sichuan": "Asia/Shanghai",
    "山东": "Asia/Shanghai",
    "shandong": "Asia/Shanghai",
    "河南": "Asia/Shanghai",
    "henan": "Asia/Shanghai",
    "湖北": "Asia/Shanghai",
    "hubei": "Asia/Shanghai",
    "湖南": "Asia/Shanghai",
    "hunan": "Asia/Shanghai",
    "福建": "Asia/Shanghai",
    "fujian": "Asia/Shanghai",
    "安徽": "Asia/Shanghai",
    "anhui": "Asia/Shanghai",
    "江西": "Asia/Shanghai",
    "jiangxi": "Asia/Shanghai",
    "山西": "Asia/Shanghai",
    "shanxi": "Asia/Shanghai",
    "陕西": "Asia/Shanghai",
    "shaanxi": "Asia/Shanghai",
    "河北": "Asia/Shanghai",
    "hebei": "Asia/Shanghai",
    "辽宁": "Asia/Shanghai",
    "liaoning": "Asia/Shanghai",
    "吉林": "Asia/Shanghai",
    "jilin": "Asia/Shanghai",
    "黑龙江": "Asia/Shanghai",
    "heilongjiang": "Asia/Shanghai",
    "云南": "Asia/Shanghai",
    "yunnan": "Asia/Shanghai",
    "贵州": "Asia/Shanghai",
    "guizhou": "Asia/Shanghai",
    "广西": "Asia/Shanghai",
    "guangxi": "Asia/Shanghai",
    "甘肃": "Asia/Shanghai",
    "gansu": "Asia/Shanghai",
    "青海": "Asia/Shanghai",
    "qinghai": "Asia/Shanghai",
    "宁夏": "Asia/Shanghai",
    "ningxia": "Asia/Shanghai",
    "内蒙古": "Asia/Shanghai",
    "innermongolia": "Asia/Shanghai",
    "海南": "Asia/Shanghai",
    "hainan": "Asia/Shanghai",
    "西藏": "Asia/Shanghai",
    "tibet": "Asia/Shanghai",
    "新疆": "Asia/Urumqi",
    "xinjiang": "Asia/Urumqi",
    "乌鲁木齐": "Asia/Urumqi",
    "urumqi": "Asia/Urumqi",
    "香港": "Asia/Hong_Kong",
    "hongkong": "Asia/Hong_Kong",
    "hk": "Asia/Hong_Kong",
    "澳门": "Asia/Macau",
    "macau": "Asia/Macau",
    "macao": "Asia/Macau",
    "台湾": "Asia/Taipei",
    "taiwan": "Asia/Taipei",
    "台北": "Asia/Taipei",
    "taipei": "Asia/Taipei",
    # ---- East Asia / Southeast Asia ----
    "日本": "Asia/Tokyo",
    "japan": "Asia/Tokyo",
    "东京": "Asia/Tokyo",
    "tokyo": "Asia/Tokyo",
    "大阪": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "京都": "Asia/Tokyo",
    "kyoto": "Asia/Tokyo",
    "札幌": "Asia/Tokyo",
    "sapporo": "Asia/Tokyo",
    "名古屋": "Asia/Tokyo",
    "nagoya": "Asia/Tokyo",
    "韩国": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "southkorea": "Asia/Seoul",
    "首尔": "Asia/Seoul",
    "seoul": "Asia/Seoul",
    "釜山": "Asia/Seoul",
    "busan": "Asia/Seoul",
    "朝鲜": "Asia/Pyongyang",
    "平壤": "Asia/Pyongyang",
    "pyongyang": "Asia/Pyongyang",
    "蒙古": "Asia/Ulaanbaatar",
    "乌兰巴托": "Asia/Ulaanbaatar",
    "ulaanbaatar": "Asia/Ulaanbaatar",
    "新加坡": "Asia/Singapore",
    "singapore": "Asia/Singapore",
    "马来西亚": "Asia/Kuala_Lumpur",
    "malaysia": "Asia/Kuala_Lumpur",
    "吉隆坡": "Asia/Kuala_Lumpur",
    "kualalumpur": "Asia/Kuala_Lumpur",
    "泰国": "Asia/Bangkok",
    "thailand": "Asia/Bangkok",
    "曼谷": "Asia/Bangkok",
    "bangkok": "Asia/Bangkok",
    "越南": "Asia/Ho_Chi_Minh",
    "vietnam": "Asia/Ho_Chi_Minh",
    "河内": "Asia/Ho_Chi_Minh",
    "hanoi": "Asia/Ho_Chi_Minh",
    "胡志明市": "Asia/Ho_Chi_Minh",
    "西贡": "Asia/Ho_Chi_Minh",
    "hochiminh": "Asia/Ho_Chi_Minh",
    "菲律宾": "Asia/Manila",
    "philippines": "Asia/Manila",
    "马尼拉": "Asia/Manila",
    "manila": "Asia/Manila",
    "印尼": "Asia/Jakarta",
    "印度尼西亚": "Asia/Jakarta",
    "indonesia": "Asia/Jakarta",
    "雅加达": "Asia/Jakarta",
    "jakarta": "Asia/Jakarta",
    "巴厘岛": "Asia/Makassar",
    "bali": "Asia/Makassar",
    "缅甸": "Asia/Yangon",
    "仰光": "Asia/Yangon",
    "yangon": "Asia/Yangon",
    "柬埔寨": "Asia/Phnom_Penh",
    "金边": "Asia/Phnom_Penh",
    "phnompenh": "Asia/Phnom_Penh",
    "文莱": "Asia/Brunei",
    "brunei": "Asia/Brunei",
    # ---- South Asia / Middle East ----
    "印度": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "新德里": "Asia/Kolkata",
    "德里": "Asia/Kolkata",
    "newdelhi": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "孟买": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "班加罗尔": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "巴基斯坦": "Asia/Karachi",
    "pakistan": "Asia/Karachi",
    "卡拉奇": "Asia/Karachi",
    "karachi": "Asia/Karachi",
    "伊斯兰堡": "Asia/Karachi",
    "islamabad": "Asia/Karachi",
    "孟加拉": "Asia/Dhaka",
    "孟加拉国": "Asia/Dhaka",
    "达卡": "Asia/Dhaka",
    "dhaka": "Asia/Dhaka",
    "斯里兰卡": "Asia/Colombo",
    "科伦坡": "Asia/Colombo",
    "colombo": "Asia/Colombo",
    "尼泊尔": "Asia/Kathmandu",
    "加德满都": "Asia/Kathmandu",
    "kathmandu": "Asia/Kathmandu",
    "阿联酋": "Asia/Dubai",
    "迪拜": "Asia/Dubai",
    "dubai": "Asia/Dubai",
    "阿布扎比": "Asia/Dubai",
    "abudhabi": "Asia/Dubai",
    "沙特": "Asia/Riyadh",
    "沙特阿拉伯": "Asia/Riyadh",
    "利雅得": "Asia/Riyadh",
    "riyadh": "Asia/Riyadh",
    "卡塔尔": "Asia/Qatar",
    "多哈": "Asia/Qatar",
    "doha": "Asia/Qatar",
    "科威特": "Asia/Kuwait",
    "kuwait": "Asia/Kuwait",
    "以色列": "Asia/Jerusalem",
    "israel": "Asia/Jerusalem",
    "特拉维夫": "Asia/Jerusalem",
    "telaviv": "Asia/Jerusalem",
    "耶路撒冷": "Asia/Jerusalem",
    "jerusalem": "Asia/Jerusalem",
    "伊朗": "Asia/Tehran",
    "德黑兰": "Asia/Tehran",
    "tehran": "Asia/Tehran",
    "土耳其": "Europe/Istanbul",
    "turkey": "Europe/Istanbul",
    "伊斯坦布尔": "Europe/Istanbul",
    "istanbul": "Europe/Istanbul",
    # ---- Europe ----
    "英国": "Europe/London",
    "uk": "Europe/London",
    "england": "Europe/London",
    "britain": "Europe/London",
    "伦敦": "Europe/London",
    "london": "Europe/London",
    "苏格兰": "Europe/London",
    "scotland": "Europe/London",
    "爱尔兰": "Europe/Dublin",
    "都柏林": "Europe/Dublin",
    "dublin": "Europe/Dublin",
    "冰岛": "Atlantic/Reykjavik",
    "雷克雅未克": "Atlantic/Reykjavik",
    "reykjavik": "Atlantic/Reykjavik",
    "法国": "Europe/Paris",
    "france": "Europe/Paris",
    "巴黎": "Europe/Paris",
    "paris": "Europe/Paris",
    "德国": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "柏林": "Europe/Berlin",
    "berlin": "Europe/Berlin",
    "慕尼黑": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "法兰克福": "Europe/Berlin",
    "frankfurt": "Europe/Berlin",
    "意大利": "Europe/Rome",
    "italy": "Europe/Rome",
    "罗马": "Europe/Rome",
    "rome": "Europe/Rome",
    "米兰": "Europe/Rome",
    "milan": "Europe/Rome",
    "西班牙": "Europe/Madrid",
    "spain": "Europe/Madrid",
    "马德里": "Europe/Madrid",
    "madrid": "Europe/Madrid",
    "巴塞罗那": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "葡萄牙": "Europe/Lisbon",
    "里斯本": "Europe/Lisbon",
    "lisbon": "Europe/Lisbon",
    "荷兰": "Europe/Amsterdam",
    "netherlands": "Europe/Amsterdam",
    "阿姆斯特丹": "Europe/Amsterdam",
    "amsterdam": "Europe/Amsterdam",
    "比利时": "Europe/Brussels",
    "布鲁塞尔": "Europe/Brussels",
    "brussels": "Europe/Brussels",
    "瑞士": "Europe/Zurich",
    "switzerland": "Europe/Zurich",
    "苏黎世": "Europe/Zurich",
    "zurich": "Europe/Zurich",
    "日内瓦": "Europe/Zurich",
    "geneva": "Europe/Zurich",
    "奥地利": "Europe/Vienna",
    "维也纳": "Europe/Vienna",
    "vienna": "Europe/Vienna",
    "瑞典": "Europe/Stockholm",
    "sweden": "Europe/Stockholm",
    "斯德哥尔摩": "Europe/Stockholm",
    "stockholm": "Europe/Stockholm",
    "挪威": "Europe/Oslo",
    "norway": "Europe/Oslo",
    "奥斯陆": "Europe/Oslo",
    "oslo": "Europe/Oslo",
    "丹麦": "Europe/Copenhagen",
    "哥本哈根": "Europe/Copenhagen",
    "copenhagen": "Europe/Copenhagen",
    "芬兰": "Europe/Helsinki",
    "finland": "Europe/Helsinki",
    "赫尔辛基": "Europe/Helsinki",
    "helsinki": "Europe/Helsinki",
    "波兰": "Europe/Warsaw",
    "poland": "Europe/Warsaw",
    "华沙": "Europe/Warsaw",
    "warsaw": "Europe/Warsaw",
    "希腊": "Europe/Athens",
    "greece": "Europe/Athens",
    "雅典": "Europe/Athens",
    "athens": "Europe/Athens",
    "匈牙利": "Europe/Budapest",
    "布达佩斯": "Europe/Budapest",
    "budapest": "Europe/Budapest",
    "捷克": "Europe/Prague",
    "布拉格": "Europe/Prague",
    "prague": "Europe/Prague",
    "罗马尼亚": "Europe/Bucharest",
    "布加勒斯特": "Europe/Bucharest",
    "bucharest": "Europe/Bucharest",
    "保加利亚": "Europe/Sofia",
    "索非亚": "Europe/Sofia",
    "sofia": "Europe/Sofia",
    "塞尔维亚": "Europe/Belgrade",
    "贝尔格莱德": "Europe/Belgrade",
    "belgrade": "Europe/Belgrade",
    "克罗地亚": "Europe/Zagreb",
    "萨格勒布": "Europe/Zagreb",
    "zagreb": "Europe/Zagreb",
    "卢森堡": "Europe/Luxembourg",
    "luxembourg": "Europe/Luxembourg",
    "俄罗斯": "Europe/Moscow",
    "russia": "Europe/Moscow",
    "莫斯科": "Europe/Moscow",
    "moscow": "Europe/Moscow",
    "圣彼得堡": "Europe/Moscow",
    "stpetersburg": "Europe/Moscow",
    "乌克兰": "Europe/Kyiv",
    "ukraine": "Europe/Kyiv",
    "基辅": "Europe/Kyiv",
    "kyiv": "Europe/Kyiv",
    "kiev": "Europe/Kyiv",
    "白俄罗斯": "Europe/Minsk",
    "明斯克": "Europe/Minsk",
    "minsk": "Europe/Minsk",
    # ---- Americas ----
    "美国": "America/New_York",
    "usa": "America/New_York",
    "us": "America/New_York",
    "america": "America/New_York",
    "unitedstates": "America/New_York",
    "美国东部": "America/New_York",
    "东海岸": "America/New_York",
    "东部时间": "America/New_York",
    "纽约": "America/New_York",
    "newyork": "America/New_York",
    "nyc": "America/New_York",
    "华盛顿": "America/New_York",
    "washington": "America/New_York",
    "washington dc": "America/New_York",
    "波士顿": "America/New_York",
    "boston": "America/New_York",
    "迈阿密": "America/New_York",
    "miami": "America/New_York",
    "亚特兰大": "America/New_York",
    "atlanta": "America/New_York",
    "费城": "America/New_York",
    "philadelphia": "America/New_York",
    "美国中部": "America/Chicago",
    "中部时间": "America/Chicago",
    "芝加哥": "America/Chicago",
    "chicago": "America/Chicago",
    "达拉斯": "America/Chicago",
    "dallas": "America/Chicago",
    "休斯顿": "America/Chicago",
    "houston": "America/Chicago",
    "奥斯汀": "America/Chicago",
    "austin": "America/Chicago",
    "美国山地": "America/Denver",
    "山地时间": "America/Denver",
    "丹佛": "America/Denver",
    "denver": "America/Denver",
    "美国西部": "America/Los_Angeles",
    "西海岸": "America/Los_Angeles",
    "太平洋时间": "America/Los_Angeles",
    "洛杉矶": "America/Los_Angeles",
    "losangeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "旧金山": "America/Los_Angeles",
    "三藩市": "America/Los_Angeles",
    "sanfrancisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "硅谷": "America/Los_Angeles",
    "siliconvalley": "America/Los_Angeles",
    "西雅图": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "拉斯维加斯": "America/Los_Angeles",
    "lasvegas": "America/Los_Angeles",
    "圣迭戈": "America/Los_Angeles",
    "sandiego": "America/Los_Angeles",
    "亚利桑那": "America/Phoenix",
    "凤凰城": "America/Phoenix",
    "phoenix": "America/Phoenix",
    "夏威夷": "Pacific/Honolulu",
    "檀香山": "Pacific/Honolulu",
    "火奴鲁鲁": "Pacific/Honolulu",
    "honolulu": "Pacific/Honolulu",
    "hawaii": "Pacific/Honolulu",
    "阿拉斯加": "America/Anchorage",
    "alaska": "America/Anchorage",
    "安克雷奇": "America/Anchorage",
    "anchorage": "America/Anchorage",
    "加拿大": "America/Toronto",
    "canada": "America/Toronto",
    "多伦多": "America/Toronto",
    "toronto": "America/Toronto",
    "渥太华": "America/Toronto",
    "ottawa": "America/Toronto",
    "蒙特利尔": "America/Toronto",
    "montreal": "America/Toronto",
    "温哥华": "America/Vancouver",
    "vancouver": "America/Vancouver",
    "卡尔加里": "America/Edmonton",
    "calgary": "America/Edmonton",
    "墨西哥": "America/Mexico_City",
    "mexico": "America/Mexico_City",
    "墨西哥城": "America/Mexico_City",
    "mexicocity": "America/Mexico_City",
    "巴西": "America/Sao_Paulo",
    "brazil": "America/Sao_Paulo",
    "圣保罗": "America/Sao_Paulo",
    "saopaulo": "America/Sao_Paulo",
    "里约热内卢": "America/Sao_Paulo",
    "里约": "America/Sao_Paulo",
    "riodejaneiro": "America/Sao_Paulo",
    "阿根廷": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires",
    "布宜诺斯艾利斯": "America/Argentina/Buenos_Aires",
    "buenosaires": "America/Argentina/Buenos_Aires",
    "智利": "America/Santiago",
    "chile": "America/Santiago",
    "秘鲁": "America/Lima",
    "利马": "America/Lima",
    "lima": "America/Lima",
    "哥伦比亚": "America/Bogota",
    "波哥大": "America/Bogota",
    "bogota": "America/Bogota",
    # ---- Africa ----
    "埃及": "Africa/Cairo",
    "开罗": "Africa/Cairo",
    "cairo": "Africa/Cairo",
    "南非": "Africa/Johannesburg",
    "southafrica": "Africa/Johannesburg",
    "约翰内斯堡": "Africa/Johannesburg",
    "johannesburg": "Africa/Johannesburg",
    "开普敦": "Africa/Johannesburg",
    "capetown": "Africa/Johannesburg",
    "尼日利亚": "Africa/Lagos",
    "拉各斯": "Africa/Lagos",
    "lagos": "Africa/Lagos",
    "肯尼亚": "Africa/Nairobi",
    "内罗毕": "Africa/Nairobi",
    "nairobi": "Africa/Nairobi",
    "摩洛哥": "Africa/Casablanca",
    "卡萨布兰卡": "Africa/Casablanca",
    "casablanca": "Africa/Casablanca",
    "埃塞俄比亚": "Africa/Addis_Ababa",
    "亚的斯亚贝巴": "Africa/Addis_Ababa",
    "addisababa": "Africa/Addis_Ababa",
    # ---- Oceania ----
    "澳大利亚": "Australia/Sydney",
    "澳洲": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "悉尼": "Australia/Sydney",
    "sydney": "Australia/Sydney",
    "墨尔本": "Australia/Melbourne",
    "melbourne": "Australia/Melbourne",
    "堪培拉": "Australia/Sydney",
    "canberra": "Australia/Sydney",
    "布里斯班": "Australia/Brisbane",
    "brisbane": "Australia/Brisbane",
    "珀斯": "Australia/Perth",
    "perth": "Australia/Perth",
    "阿德莱德": "Australia/Adelaide",
    "adelaide": "Australia/Adelaide",
    "达尔文": "Australia/Darwin",
    "darwin": "Australia/Darwin",
    "霍巴特": "Australia/Hobart",
    "hobart": "Australia/Hobart",
    "新西兰": "Pacific/Auckland",
    "newzealand": "Pacific/Auckland",
    "奥克兰": "Pacific/Auckland",
    "auckland": "Pacific/Auckland",
    "惠灵顿": "Pacific/Auckland",
    "wellington": "Pacific/Auckland",
    "斐济": "Pacific/Fiji",
    "fiji": "Pacific/Fiji",
    # ---- Universal time ----
    "utc": "UTC",
    "gmt": "UTC",
    "zulu": "UTC",
    "世界时": "UTC",
    "世界标准时间": "UTC",
    "协调世界时": "UTC",
    "格林尼治时间": "UTC",
    "格林威治时间": "UTC",
}

# Some aliases need an extra note (key: alias, value: additional explanation)
_ALIAS_NOTES: dict[str, str] = {
    "美国": "The United States spans multiple time zones and defaults to Eastern Time (America/New_York); for the West Coast use \"洛杉矶\" or \"美国西部\".",
    "usa": "The United States spans multiple time zones and defaults to Eastern Time (America/New_York); for the West Coast use \"洛杉矶\" or \"美国西部\".",
    "us": "The United States spans multiple time zones and defaults to Eastern Time (America/New_York); for the West Coast use \"洛杉矶\" or \"美国西部\".",
    "america": "The United States spans multiple time zones and defaults to Eastern Time (America/New_York); for the West Coast use \"洛杉矶\" or \"美国西部\".",
    "unitedstates": "The United States spans multiple time zones and defaults to Eastern Time (America/New_York); for the West Coast use \"洛杉矶\" or \"美国西部\".",
    "新疆": "Xinjiang commonly uses UTC+6 Urumqi time; for Beijing time use \"北京\" or Asia/Shanghai.",
    "xinjiang": "Xinjiang commonly uses UTC+6 Urumqi time; for Beijing time use \"北京\" or Asia/Shanghai.",
    "乌鲁木齐": "Urumqi uses UTC+6; for Beijing time use \"北京\" or Asia/Shanghai.",
    "urumqi": "Urumqi uses UTC+6; for Beijing time use \"北京\" or Asia/Shanghai.",
}

# ---------------------------------------------------------------------------
# Fixed-offset table used when tzdata is missing (hours, observes DST)
# Only a fallback when the standard library cannot load IANA data; results are
# annotated with "daylight saving time not applied".
# ---------------------------------------------------------------------------
_FALLBACK_OFFSETS: dict[str, tuple[float, bool]] = {
    "UTC": (0.0, False),
    "Asia/Shanghai": (8.0, False),
    "Asia/Hong_Kong": (8.0, False),
    "Asia/Macau": (8.0, False),
    "Asia/Taipei": (8.0, False),
    "Asia/Urumqi": (6.0, False),
    "Asia/Tokyo": (9.0, False),
    "Asia/Seoul": (9.0, False),
    "Asia/Pyongyang": (9.0, False),
    "Asia/Singapore": (8.0, False),
    "Asia/Kuala_Lumpur": (8.0, False),
    "Asia/Bangkok": (7.0, False),
    "Asia/Ho_Chi_Minh": (7.0, False),
    "Asia/Manila": (8.0, False),
    "Asia/Jakarta": (7.0, False),
    "Asia/Makassar": (8.0, False),
    "Asia/Yangon": (6.5, False),
    "Asia/Phnom_Penh": (7.0, False),
    "Asia/Brunei": (8.0, False),
    "Asia/Ulaanbaatar": (8.0, False),
    "Asia/Kolkata": (5.5, False),
    "Asia/Karachi": (5.0, False),
    "Asia/Dhaka": (6.0, False),
    "Asia/Colombo": (5.5, False),
    "Asia/Kathmandu": (5.75, False),
    "Asia/Dubai": (4.0, False),
    "Asia/Riyadh": (3.0, False),
    "Asia/Qatar": (3.0, False),
    "Asia/Kuwait": (3.0, False),
    "Asia/Jerusalem": (2.0, True),
    "Asia/Tehran": (3.5, False),
    "Europe/Istanbul": (3.0, False),
    "Europe/Moscow": (3.0, False),
    "Europe/Minsk": (3.0, False),
    "Europe/Kyiv": (2.0, True),
    "Europe/London": (0.0, True),
    "Europe/Dublin": (0.0, True),
    "Europe/Paris": (1.0, True),
    "Europe/Berlin": (1.0, True),
    "Europe/Rome": (1.0, True),
    "Europe/Madrid": (1.0, True),
    "Europe/Lisbon": (0.0, True),
    "Europe/Amsterdam": (1.0, True),
    "Europe/Brussels": (1.0, True),
    "Europe/Zurich": (1.0, True),
    "Europe/Vienna": (1.0, True),
    "Europe/Prague": (1.0, True),
    "Europe/Budapest": (1.0, True),
    "Europe/Warsaw": (1.0, True),
    "Europe/Stockholm": (1.0, True),
    "Europe/Oslo": (1.0, True),
    "Europe/Copenhagen": (1.0, True),
    "Europe/Helsinki": (2.0, True),
    "Europe/Athens": (2.0, True),
    "Europe/Bucharest": (2.0, True),
    "Europe/Belgrade": (1.0, True),
    "Europe/Zagreb": (1.0, True),
    "Europe/Sofia": (2.0, True),
    "Europe/Luxembourg": (1.0, True),
    "Atlantic/Reykjavik": (0.0, False),
    "Africa/Cairo": (2.0, True),
    "Africa/Johannesburg": (2.0, False),
    "Africa/Lagos": (1.0, False),
    "Africa/Nairobi": (3.0, False),
    "Africa/Casablanca": (1.0, False),
    "Africa/Addis_Ababa": (3.0, False),
    "America/New_York": (-5.0, True),
    "America/Chicago": (-6.0, True),
    "America/Denver": (-7.0, True),
    "America/Phoenix": (-7.0, False),
    "America/Los_Angeles": (-8.0, True),
    "America/Anchorage": (-9.0, True),
    "America/Toronto": (-5.0, True),
    "America/Vancouver": (-8.0, True),
    "America/Edmonton": (-7.0, True),
    "America/Mexico_City": (-6.0, False),
    "America/Sao_Paulo": (-3.0, False),
    "America/Argentina/Buenos_Aires": (-3.0, False),
    "America/Santiago": (-4.0, True),
    "America/Lima": (-5.0, False),
    "America/Bogota": (-5.0, False),
    "Pacific/Honolulu": (-10.0, False),
    "Pacific/Auckland": (12.0, True),
    "Pacific/Fiji": (12.0, False),
    "Australia/Sydney": (10.0, True),
    "Australia/Melbourne": (10.0, True),
    "Australia/Brisbane": (10.0, False),
    "Australia/Adelaide": (9.5, True),
    "Australia/Perth": (8.0, False),
    "Australia/Darwin": (9.5, False),
    "Australia/Hobart": (10.0, True),
}

# Local-time spellings
_LOCAL_NAMES = {
    "local",
    "localtime",
    "localtimezone",
    "system",
    "systemtime",
    "server",
    "host",
    "machine",
    "here",
    "本机",
    "本机时间",
    "本地",
    "本地时间",
    "系统",
    "系统时间",
    "服务器时间",
    "这台机器",
}

_STRIP_CHARS = re.compile(r"[\s\-_·'’./\\]+")
_NORMALIZED_LOCAL = {_STRIP_CHARS.sub("", name) for name in _LOCAL_NAMES}

_OFFSET_RE = re.compile(
    r"^(?:(?:utc|gmt)\s*)?([+-])\s*(\d{1,2})(?::?(\d{2}))?$",
    re.IGNORECASE,
)
_CN_OFFSET_RE = re.compile(r"^([东西])\s*(\d{1,2}|[一二三四五六七八九十]{1,3})\s*区?$")
_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}
_MAX_OFFSET_HOURS = 14
_LIST_LIMIT = 40

# list_timezones allows coarse filtering by continent keyword
_CONTINENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "亚洲": ("asia/",),
    "欧洲": ("europe/",),
    "非洲": ("africa/",),
    "美洲": ("america/",),
    "北美洲": ("america/",),
    "南美洲": ("america/",),
    "北美": ("america/",),
    "南美": ("america/",),
    "大洋洲": ("australia/", "pacific/"),
}


class _UnknownZone(ValueError):
    """Raised when the input cannot be parsed as a time zone; carries the data needed for candidate suggestions."""

    def __init__(self, requested: str, extra: str = "") -> None:
        super().__init__(requested)
        self.requested = requested
        self.extra = extra


@dataclass(frozen=True)
class _ResolvedZone:
    """Resolution result: time zone object plus display name, alias, and notes."""

    tz: tzinfo
    label: str
    alias: str | None = None
    notes: tuple[str, ...] = ()
    dst_hint: bool | None = None


def _norm(text: str) -> str:
    """Normalize by removing whitespace, hyphens, quotes, and slashes for lenient matching."""
    return _STRIP_CHARS.sub("", text or "").lower()


@lru_cache(maxsize=1)
def _tzdata_available() -> bool:
    """Whether the standard library can read IANA time zone data (built in on Linux/macOS; Windows needs tzdata)."""
    for key in ("UTC", "Asia/Shanghai"):
        try:
            ZoneInfo(key)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            return False
    return True


@lru_cache(maxsize=1)
def _iana_index() -> dict[str, str]:
    """Lowercase IANA time zone name → official capitalization.

    Names from the built-in fixed-offset table are indexed as well, so that
    inconsistently cased forms such as ``asia/shanghai`` are recognized even when
    the machine has no tzdata.
    """
    index: dict[str, str] = {}
    try:
        for name in available_timezones():
            index.setdefault(name.lower(), name)
    except Exception:  # pragma: no cover - avoid crashing at import time in extreme environments
        pass
    for name in _FALLBACK_OFFSETS:
        index.setdefault(name.lower(), name)
    return index


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, tuple[str, str]]:
    """Normalized alias → (original spelling, IANA time zone name)."""
    index: dict[str, tuple[str, str]] = {}
    for alias, zone in _CITY_ALIASES.items():
        index.setdefault(_norm(alias), (alias, zone))
    return index


def _parse_offset(text: str) -> tzinfo | None:
    """Parse fixed-offset forms such as UTC+8, GMT-5, +08:00, or a Chinese offset alias."""
    raw = text.strip()

    match = _CN_OFFSET_RE.match(raw)
    if match:
        sign = 1 if match.group(1) == "东" else -1
        digits = match.group(2)
        hours = int(digits) if digits.isdigit() else _CN_DIGITS.get(digits, -1)
        if 0 <= hours <= _MAX_OFFSET_HOURS:
            return timezone(timedelta(hours=sign * hours))

    match = _OFFSET_RE.match(raw)
    if match:
        hours = int(match.group(2))
        minutes = int(match.group(3) or 0)
        if hours <= _MAX_OFFSET_HOURS and minutes < 60 and (hours < 14 or minutes == 0):
            offset = timedelta(hours=hours, minutes=minutes)
            return timezone(offset if match.group(1) == "+" else -offset)

    return None


def _zone_for(zone_name: str) -> tuple[tzinfo | None, str | None]:
    """Look up a time zone by IANA name, falling back to the fixed-offset table when no data is available.

    Returns:
        ``(tzinfo or None, extra note)``.
    """
    if _tzdata_available():
        try:
            return ZoneInfo(zone_name), None
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass

    fallback = _FALLBACK_OFFSETS.get(zone_name)
    if fallback is None:
        return None, None

    hours, observes_dst = fallback
    note: str | None = None
    if observes_dst:
        note = (
            "The IANA time zone database (tzdata) is missing on this machine, so the "
            "built-in standard-time offset was used; this region observes daylight "
            "saving time, so the result may be off by 1 hour. Run `uv sync` to install "
            "tzdata for an exact result."
        )
    return timezone(timedelta(hours=hours)), note


def _resolve(requested: str) -> _ResolvedZone:
    """Resolve user input into a time zone with a display name and notes."""
    raw = (requested or "").strip()
    if not raw:
        raise _UnknownZone(raw or "", "Please provide a region, IANA time zone name, or UTC offset.")

    # 1) Local time
    if _norm(raw) in _NORMALIZED_LOCAL:
        now = datetime.now().astimezone()
        try:
            dst_active = bool(_time.daylight and _time.localtime().tm_isdst > 0)
        except Exception:  # pragma: no cover - some platforms do not expose DST information
            dst_active = None
        return _ResolvedZone(now.tzinfo, "Local time", dst_hint=dst_active)

    # 2) UTC offset forms
    offset_tz = _parse_offset(raw)
    if offset_tz is not None:
        offset = offset_tz.utcoffset(None) or timedelta(0)
        total_minutes = int(offset.total_seconds()) // 60
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        return _ResolvedZone(offset_tz, f"UTC{sign}{hours:02d}:{minutes:02d}")

    # 3) City / province / country aliases
    alias_hit = _alias_index().get(_norm(raw))
    if alias_hit is not None:
        alias, zone_name = alias_hit
        tz, note = _zone_for(zone_name)
        if tz is None:
            raise _UnknownZone(raw, f"Missing time zone data for {zone_name}; install tzdata and retry.")
        notes = tuple(
            text for text in (_ALIAS_NOTES.get(alias), note) if text
        )
        return _ResolvedZone(tz, zone_name, alias, notes)

    # 4) IANA time zone name (case- and space-insensitive)
    iana = _iana_index()
    canonical = iana.get(raw.lower(), "") or iana.get(raw.replace(" ", "_").lower(), "")
    if canonical:
        tz, note = _zone_for(canonical)
        if tz is not None:
            return _ResolvedZone(tz, canonical, None, tuple(text for text in (note,) if text))

    # 5) Fallback: IANA name not in the index (usually no tzdata); still try once
    if "/" in raw:
        tz, note = _zone_for(raw)
        if tz is not None:
            return _ResolvedZone(tz, raw, None, tuple(text for text in (note,) if text))

    extra = ""
    if not _tzdata_available():
        extra = "The IANA time zone database (tzdata) is missing on this machine; the built-in table only covers common regions."
    raise _UnknownZone(raw, extra)


def _suggestions(text: str, limit: int = 5) -> list[str]:
    """Return nearby region / time zone candidates to help the agent self-correct."""
    normalized = _norm(text)
    if not normalized or len(normalized) < 2:
        return []

    found: list[str] = []
    for alias, zone in _CITY_ALIASES.items():
        alias_norm = _norm(alias)
        if len(alias_norm) < 2 or alias_norm == normalized:
            continue
        if alias_norm in normalized or normalized in alias_norm:
            entry = f"{alias} ({zone})"
            if entry not in found:
                found.append(entry)
            if len(found) >= limit:
                return found

    pool = list(_CITY_ALIASES) + list(_iana_index().values())
    # A one-character difference between short Chinese names scores about 0.5,
    # so relax the threshold for short inputs.
    cutoff = 0.5 if len(normalized) <= 3 else 0.6
    scored: list[tuple[float, int, int, str]] = []
    seen: set[str] = set()
    for order, candidate in enumerate(pool):
        if candidate in seen:
            continue
        seen.add(candidate)
        score = difflib.SequenceMatcher(None, text, candidate).ratio()
        if score < cutoff:
            continue
        # On a tie, prefer candidates closer in length and earlier in the alias
        # table (usually the more common spelling).
        scored.append((score, -abs(len(candidate) - len(text)), -order, candidate))
    scored.sort(reverse=True)

    for _, _, _, candidate in scored:
        zone = _CITY_ALIASES.get(candidate)
        entry = f"{candidate} ({zone})" if zone else candidate
        if entry not in found:
            found.append(entry)
        if len(found) >= limit:
            break
    return found


def _error_message(requested: str, extra: str = "") -> str:
    if not (requested or "").strip():
        return tool_error(
            "zone_empty",
            "Provide the region or time zone to query.",
            hint='"local" (本地), "北京", "Asia/Shanghai", or "UTC+8".',
        )
    hints = [
        "Supports local/本地, IANA time zone names (Asia/Shanghai, UTC), "
        "common cities or countries (北京, 纽约, 日本), and UTC offsets "
        "(UTC+8, +08:00, 东八区)."
    ]
    suggestions = _suggestions(requested)
    if suggestions:
        hints.append("Close matches: " + ", ".join(suggestions) + ".")
    if extra:
        hints.append(extra)
    return tool_error("zone_unknown", f"Unrecognized time zone/region {requested!r}.", hint=" ".join(hints))


def _format(now: datetime, zone: _ResolvedZone) -> str:
    """Format the time as multi-line text friendly to both humans and the agent."""
    offset = now.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds()) // 60
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    offset_text = f"UTC{sign}{hours:02d}:{minutes:02d}"

    if zone.dst_hint is not None:
        dst_active = zone.dst_hint
    else:
        dst_active = (now.dst() or timedelta(0)) > timedelta(0)

    tz_display = now.tzname() or ""
    if tz_display == offset_text or (tz_display == "UTC" and offset_text == "UTC+00:00"):
        # For fixed-offset zones (the no-tzdata fallback) the abbreviation already
        # is UTC±HH:MM, so avoid showing it twice.
        tz_display = ""
    tz_part = f"{tz_display}, " if tz_display else ""

    lines: list[str] = []
    if zone.alias and zone.alias != zone.label and _norm(zone.alias) not in _NORMALIZED_LOCAL:
        lines.append(f"Location: {zone.alias} → {zone.label}")
    else:
        lines.append(f"Location: {zone.label}")
    lines.append(f"Time: {now.strftime('%Y-%m-%d %H:%M:%S')} ({_WEEKDAYS[now.weekday()]})")
    lines.append(
        f"Time zone: {tz_part}{offset_text}, DST: {'yes' if dst_active else 'no'}"
    )
    lines.append(f"ISO 8601: {now.isoformat(timespec='seconds')}")
    lines.append(f"Unix timestamp: {int(now.timestamp())}")
    for note in zone.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


@tool
def get_current_time(timezone_name: str = "local") -> str:
    """Get the current time for a specified region / time zone or for the local machine.

    Args:
        timezone_name: Region or time zone to query; defaults to "local" for the
            local machine time. Also accepts IANA time zone names ("Asia/Shanghai",
            "UTC"), Chinese or English names of common cities or countries, or
            UTC offsets ("UTC+8", "+08:00", or a Chinese offset alias).

    Returns:
        Multi-line text with the region/time zone, date and time, weekday, time
        zone abbreviation and UTC offset, DST flag, ISO 8601 text, and Unix
        timestamp. When the input cannot be recognized, returns a message
        starting with "Error:" (including nearby candidates).
    """
    try:
        zone = _resolve(timezone_name)
    except _UnknownZone as exc:
        return _error_message(exc.requested, exc.extra)

    try:
        now = datetime.now(zone.tz)
    except Exception as exc:  # fallback: do not crash the agent with an uncaught exception
        return tool_error("time_failed", f"Failed to get the time ({type(exc).__name__}: {exc}).")
    return _format(now, zone)


@tool
def list_timezones(keyword: str = "") -> str:
    """List supported region names and their IANA time zones, optionally filtered by keyword.

    Args:
        keyword: Filter keyword such as "United States", "Europe", "Asia/", or a
            Chinese region name; leave empty to list common region examples.

    Returns:
        One "region → IANA time zone" pair per line, plus the data source and usage hint.
    """
    text = (keyword or "").strip()
    needle = _norm(text)
    if text and not needle:
        return _error_message(text)

    needles = tuple(
        _norm(item) for item in _CONTINENT_KEYWORDS.get(needle, (text,))
    )
    matches: list[str] = []
    seen: set[str] = set()
    total = 0
    for alias, zone in _CITY_ALIASES.items():
        if needle and not any(
            item in _norm(alias) or item in zone.lower() for item in needles
        ):
            continue
        entry = f"{alias} → {zone}"
        if entry in seen:
            continue
        seen.add(entry)
        total += 1
        if len(matches) < _LIST_LIMIT:
            matches.append(entry)

    source = (
        "IANA time zone database (tzdata) loaded"
        if _tzdata_available()
        else "tzdata not detected (run `uv sync` on Windows); only built-in common time zones are available"
    )
    if not matches:
        return tool_error(
            "no_match",
            f"No region or time zone matched {text!r}.",
            hint="Try a country/city name (美国, 东京) or an IANA prefix (Asia/, Europe/).",
        )

    header = f"Data source: {source}. {len(_CITY_ALIASES)} region aliases in total, {total} matched"
    if total > len(matches):
        header += f"; showing the first {len(matches)}"
    hint = 'Usage: get_current_time(timezone_name="Asia/Shanghai"), or pass a city name such as "上海".'
    return "\n".join([header, *matches, hint])
