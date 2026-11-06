# tests/time_test.py
"""tools/time.py 的单元测试。

运行方式（在项目根目录）:
    .venv\\Scripts\\python.exe -m unittest discover -s tests -p "*_test.py" -v

测试同时覆盖两条路径：
- 有 IANA 时区数据（tzdata / Linux / macOS）时走 zoneinfo；
- 没有 tzdata 时走内置固定偏移表（Windows 默认情况）。
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from tools import time as time_tool


class TimeToolLocalTest(unittest.TestCase):
    """本地时间相关用例。"""

    def test_default_is_local(self):
        text = time_tool.get_current_time.invoke({})
        self.assertIn("地区：本地时间", text)
        self.assertIn("时间：", text)

    def test_local_aliases(self):
        for name in ("local", "本地", "本机", "系统时间", "LOCAL"):
            with self.subTest(name=name):
                self.assertEqual(time_tool._resolve(name).label, "本地时间")

    def test_local_output_has_iso_and_unix(self):
        text = time_tool.get_current_time.invoke({"timezone_name": "local"})
        self.assertIn("ISO 8601：", text)
        self.assertIn("Unix 时间戳：", text)
        self.assertRegex(text, r"时间：\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}（星期[一二三四五六日]）")


class TimeToolRegionTest(unittest.TestCase):
    """地区 / 城市 / IANA 时区解析用例。"""

    def test_chinese_city_alias(self):
        zone = time_tool._resolve("北京")
        self.assertEqual(zone.label, "Asia/Shanghai")
        now = datetime.now(zone.tz)
        self.assertEqual(now.utcoffset(), timedelta(hours=8))

        text = time_tool.get_current_time.invoke({"timezone_name": "北京"})
        self.assertIn("地区：北京 → Asia/Shanghai", text)

    def test_english_city_alias(self):
        zone = time_tool._resolve("New York")
        self.assertEqual(zone.label, "America/New_York")

    def test_country_alias(self):
        self.assertEqual(time_tool._resolve("日本").label, "Asia/Tokyo")
        self.assertEqual(time_tool._resolve("美国").label, "America/New_York")

    def test_alias_with_note(self):
        text = time_tool.get_current_time.invoke({"timezone_name": "美国"})
        self.assertIn("美国横跨多个时区", text)

    def test_iana_case_insensitive(self):
        for name in ("Asia/Shanghai", "asia/shanghai", "ASIA/SHANGHAI"):
            with self.subTest(name=name):
                self.assertEqual(time_tool._resolve(name).label, "Asia/Shanghai")

    def test_iana_utc(self):
        zone = time_tool._resolve("UTC")
        self.assertEqual(zone.label, "UTC")
        self.assertEqual(datetime.now(zone.tz).utcoffset(), timedelta(0))

    def test_tokyo_offset(self):
        text = time_tool.get_current_time.invoke({"timezone_name": "Asia/Tokyo"})
        self.assertIn("UTC+09:00", text)
        self.assertIn("ISO 8601：", text)

    def test_dst_flag_is_consistent_with_zone(self):
        zone = time_tool._resolve("America/New_York")
        now = datetime.now(zone.tz)
        expected = "是" if (now.dst() or timedelta(0)) > timedelta(0) else "否"
        self.assertIn(f"夏令时：{expected}", time_tool._format(now, zone))

    def test_no_dst_zone(self):
        zone = time_tool._resolve("Asia/Shanghai")
        now = datetime.now(zone.tz)
        self.assertIn("夏令时：否", time_tool._format(now, zone))


class TimeToolOffsetTest(unittest.TestCase):
    """UTC 偏移写法用例。"""

    def test_offset_formats(self):
        cases = {
            "UTC+8": timedelta(hours=8),
            "utc-5": timedelta(hours=-5),
            "+08:00": timedelta(hours=8),
            "-03:00": timedelta(hours=-3),
            "GMT+05:30": timedelta(hours=5, minutes=30),
            "东八区": timedelta(hours=8),
            "西五区": timedelta(hours=-5),
            "东十二区": timedelta(hours=12),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                tz = time_tool._parse_offset(text)
                self.assertIsNotNone(tz, text)
                self.assertEqual(tz.utcoffset(None), expected)

    def test_offset_out_of_range(self):
        self.assertIsNone(time_tool._parse_offset("UTC+15"))

    def test_offset_output(self):
        text = time_tool.get_current_time.invoke({"timezone_name": "东八区"})
        self.assertIn("地区：UTC+08:00", text)
        self.assertIn("UTC+08:00", text)


class TimeToolErrorTest(unittest.TestCase):
    """无法识别输入时的兜底行为。"""

    def test_unknown_zone_lists_suggestions(self):
        text = time_tool.get_current_time.invoke({"timezone_name": "尚海"})
        self.assertTrue(text.startswith("Error:"), text)
        self.assertIn("上海", text)

    def test_empty_input(self):
        text = time_tool.get_current_time.invoke({"timezone_name": "   "})
        self.assertTrue(text.startswith("Error:"), text)
        self.assertIn("请提供", text)

    def test_unknown_zone_does_not_raise(self):
        text = time_tool.get_current_time.invoke({"timezone_name": "Europe/Nowhere"})
        self.assertTrue(text.startswith("Error:"), text)


class TimeToolFallbackTest(unittest.TestCase):
    """模拟没有 tzdata 的 Windows 环境，验证固定偏移兜底。"""

    def _without_tzdata(self, name: str) -> str:
        with patch.object(time_tool, "_tzdata_available", return_value=False):
            return time_tool.get_current_time.invoke({"timezone_name": name})

    def test_fallback_returns_fixed_offset_with_note(self):
        text = self._without_tzdata("纽约")
        self.assertIn("America/New_York", text)
        self.assertIn("UTC-05:00", text)
        self.assertIn("注意：", text)
        self.assertIn("tzdata", text)

    def test_fallback_resolves_lowercase_iana(self):
        with patch.object(time_tool, "_tzdata_available", return_value=False):
            zone = time_tool._resolve("asia/shanghai")
        self.assertEqual(zone.label, "Asia/Shanghai")
        self.assertEqual(datetime.now(zone.tz).utcoffset(), timedelta(hours=8))

    def test_fallback_unknown_zone_mentions_tzdata(self):
        with patch.object(time_tool, "_tzdata_available", return_value=False):
            with self.assertRaises(time_tool._UnknownZone) as ctx:
                time_tool._resolve("Europe/Nowhere")
        self.assertIn("tzdata", ctx.exception.extra)


class ListTimezonesTest(unittest.TestCase):
    """list_timezones 工具用例。"""

    def test_keyword_filter(self):
        text = time_tool.list_timezones.invoke({"keyword": "美国"})
        self.assertIn("美国 → America/New_York", text)
        self.assertIn("数据源", text)
        self.assertIn("用法", text)

    def test_empty_keyword_lists_samples(self):
        text = time_tool.list_timezones.invoke({})
        self.assertIn("→", text)
        self.assertLessEqual(len(text.splitlines()), time_tool._LIST_LIMIT + 2)

    def test_no_match_returns_error(self):
        text = time_tool.list_timezones.invoke({"keyword": "zzzz"})
        self.assertTrue(text.startswith("Error:"), text)


class ToolRegistrationTest(unittest.TestCase):
    """工具注册信息，供 Agent 正常调用。"""

    def test_tools_are_exposed_with_names_and_descriptions(self):
        self.assertEqual(time_tool.get_current_time.name, "get_current_time")
        self.assertIn("timezone_name", time_tool.get_current_time.args)
        self.assertEqual(time_tool.list_timezones.name, "list_timezones")
        self.assertTrue(time_tool.get_current_time.description)


if __name__ == "__main__":
    unittest.main()
