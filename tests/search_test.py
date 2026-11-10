# tests/search_test.py
"""网页搜索工具的测试：多服务解析、配置缺失与错误契约。"""

import unittest
from unittest.mock import patch

from tools.context import ToolContext
from tools.search import build_search_tools

_DDG_HTML = """
<html><body>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=x">Example A</a>
  </h2>
  <a class="result__snippet" href="#">A <b>snippet</b> here</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://example.org/b">Example B</a>
  </h2>
  <a class="result__snippet" href="#">B snippet</a>
</div>
</body></html>
"""


class SearchToolTest(unittest.TestCase):
    def setUp(self):
        self.ctx = ToolContext(max_output_chars=5000)

    def tool(self, config=None):
        return build_search_tools(config or {}, self.ctx)[0]

    def test_empty_query(self):
        text = self.tool().invoke({"query": "  "})
        self.assertTrue(text.startswith("Error: [empty_query]"), text)

    def test_duckduckgo_parses_results(self):
        with patch("tools.search._get_text", return_value=_DDG_HTML):
            text = self.tool({"SEARCH_PROVIDER": "duckduckgo"}).invoke({"query": "langchain"})
        self.assertIn("DuckDuckGo", text)
        self.assertIn("Example A", text)
        self.assertIn("https://example.com/a", text)
        self.assertIn("A snippet here", text)
        self.assertIn("Example B", text)
        self.assertIn("不可信", text)

    def test_bing_parses_results(self):
        bing = """
        <ol id="b_results">
          <li class="b_algo"><h2><a href="https://example.com/a">Example A</a></h2>
            <div class="b_caption"><p>A <strong>snippet</strong> here</p></div></li>
          <li class="b_algo"><h2><a href="https://example.org/b">Example B</a></h2>
            <p>B snippet</p></li>
        </ol>
        """
        with patch("tools.search._get_text", return_value=bing):
            text = self.tool().invoke({"query": "langchain"})
        self.assertIn("Bing", text)
        self.assertIn("Example A", text)
        self.assertIn("https://example.com/a", text)
        self.assertIn("A snippet here", text)
        self.assertIn("Example B", text)

    def test_tavily_requires_key(self):
        text = self.tool({"SEARCH_PROVIDER": "tavily"}).invoke({"query": "x"})
        self.assertTrue(text.startswith("Error: [search_not_configured]"), text)

    def test_tavily_payload_and_results(self):
        captured = {}

        def fake_post(url, *, payload, headers=None, timeout=15):
            captured.update(payload)
            return {"results": [{"title": "T", "url": "https://t.example", "content": "C"}]}

        with patch("tools.search._post_json", fake_post):
            text = self.tool(
                {"SEARCH_PROVIDER": "tavily", "SEARCH_API_KEY": "tvly-test", "SEARCH_MAX_RESULTS": "3"}
            ).invoke({"query": "hello"})
        self.assertEqual(captured["api_key"], "tvly-test")
        self.assertEqual(captured["max_results"], 3)
        self.assertIn("T", text)
        self.assertIn("https://t.example", text)

    def test_searxng_requires_endpoint(self):
        text = self.tool({"SEARCH_PROVIDER": "searxng"}).invoke({"query": "x"})
        self.assertTrue(text.startswith("Error: [search_not_configured]"), text)

    def test_http_error_is_tool_error(self):
        import httpx

        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(500, request=request)
        with patch("tools.search._get_text", side_effect=httpx.HTTPStatusError("boom", request=request, response=response)):
            text = self.tool().invoke({"query": "x"})
        self.assertTrue(text.startswith("Error: [search_http_error]"), text)


if __name__ == "__main__":
    unittest.main()
