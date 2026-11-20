"""Regression tests for full-query transport and explicit provider mismatches."""

from html import escape
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from tools.context import ToolContext
from tools.search import _unwrap_ddg_url, build_search_tools


QUERY = "反垄断法 2022年修正 第二十二条 滥用市场支配地位 第二十四条 推定 市场份额 二分之一"
BING_RESULTS = '<li class="b_algo"><h2><a href="https://example.com/law">Law</a></h2><p>Full text</p></li>'
DDG_RESULTS = '<a class="result__a" href="https://example.com/law">Law</a>'


class SearchQueryTest(unittest.TestCase):
    def invoke(self, provider="bing", query=QUERY):
        config = {
            "SEARCH_PROVIDER": provider,
            "SEARCH_API_KEY": "test-key",
            "SEARCH_ENDPOINT": "https://searx.example",
        }
        return build_search_tools(config, ToolContext(max_output_chars=5000))[0].invoke({"query": query})

    def transport(self, handler):
        # Keep real HTTPX request building, percent encoding, and redirect handling.
        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
        return patch("tools.search.httpx.Client", return_value=client)

    def test_full_query_survives_http_encoding_and_redirects(self):
        for provider, body in (("bing", BING_RESULTS), ("duckduckgo", DDG_RESULTS)):
            with self.subTest(provider=provider):
                requests = []

                def handler(request):
                    requests.append(request)
                    values = parse_qs(urlparse(str(request.url)).query)
                    self.assertEqual(values["q"], [QUERY])
                    self.assertIn(b"%E5%8F%8D", request.url.raw_path)
                    if len(requests) == 1:
                        # Use %20 instead of +, exercising decoding independently.
                        location = str(request.url).replace("+", "%20") + "&redirected=1"
                        return httpx.Response(302, headers={"Location": location})
                    return httpx.Response(200, text=f'<input name="q" value="{QUERY}">' + body)

                with self.transport(handler):
                    output = self.invoke(provider)
                self.assertEqual(len(requests), 2)
                self.assertIn(f"Search: {QUERY}", output)
                self.assertIn("https://example.com/law", output)

    def test_all_api_providers_receive_full_query(self):
        for provider in ("tavily", "brave", "serper", "searxng"):
            with self.subTest(provider=provider):
                requests = []

                def handler(request):
                    import json
                    requests.append(request)
                    if request.method == "POST":
                        payload = json.loads(request.content)
                        self.assertEqual(payload.get("query", payload.get("q")), QUERY)
                    else:
                        self.assertEqual(request.url.params.get_list("q"), [QUERY])
                    item = {"title": "Law", "url": "https://example.com/law", "link": "https://example.com/law"}
                    return httpx.Response(200, json={"results": [item], "organic": [item], "web": {"results": [item]}})

                with self.transport(handler):
                    output = self.invoke(provider)
                self.assertEqual(len(requests), 1)
                self.assertIn(f"Search: {QUERY}", output)

    def test_redirect_rewrite_is_reported_even_when_later_restored(self):
        for restore in (False, True):
            with self.subTest(restore=restore):
                requests = []

                def handler(request):
                    requests.append(request)
                    if len(requests) == 1:
                        self.assertEqual(request.url.params["q"], QUERY)
                        return httpx.Response(302, headers={"Location": "/search?" + urlencode({"q": "反"})})
                    if restore and len(requests) == 2:
                        return httpx.Response(302, headers={"Location": "/search?" + urlencode({"q": QUERY})})
                    return httpx.Response(200, text=BING_RESULTS)

                with self.transport(handler):
                    output = self.invoke()
                self.assertTrue(output.startswith("Error: [search_query_changed]"), output)
                self.assertIn(QUERY, output)
                self.assertIn("response URL", output)
                self.assertIn("does not establish why", output)
                self.assertNotIn("https://example.com/law", output)

    def test_query_fields_detect_rewrites_with_html_attribute_variants(self):
        for provider in ("bing", "duckduckgo"):
            for field in (
                "<INPUT VALUE='反' NAME='q' TYPE='search' />",
                '<input id="sb_form_q" value="&#21453;" type="text" name="q">',
            ):
                with self.subTest(provider=provider, field=field):
                    with patch("tools.search._get_text", return_value=field + BING_RESULTS + DDG_RESULTS):
                        output = self.invoke(provider)
                    self.assertTrue(output.startswith("Error: [search_query_changed]"), output)
                    self.assertIn("search field", output)

    def test_missing_metadata_suggestions_and_whitespace_are_not_rewrites(self):
        pages = [
            BING_RESULTS,
            '<a href="/search?q=other">Did you mean other?</a>' + BING_RESULTS,
            '<input name="q" type="hidden" value="other">' + BING_RESULTS,
            '<input name="q" value="">' + BING_RESULTS,
            f'<input name="q" value="  {QUERY.replace(" ", "  ")}  ">' + BING_RESULTS,
        ]
        for page in pages:
            with self.subTest(page=page):
                with patch("tools.search._get_text", return_value=page):
                    output = self.invoke()
                self.assertIn(f"Search: {QUERY}", output)

    def test_literal_query_characters_are_decoded_once(self):
        query = QUERY + ' C++ & "quoted" %2F'
        page = '<input name="q" value="' + escape(query, quote=True) + '">' + BING_RESULTS
        with self.transport(lambda request: httpx.Response(200, text=page)):
            output = self.invoke(query=query)
        self.assertIn(f"Search: {query}", output)

    def test_ddg_redirect_preserves_encoded_target_and_nested_query(self):
        target = "https://example.com/a%2Fb?" + urlencode({"q": QUERY, "literal": "%26+C++"})
        wrapped = "https://duckduckgo.com/l/?" + urlencode({"uddg": target})
        self.assertEqual(_unwrap_ddg_url(wrapped), target)
        page = '<a class="result__a" href="' + escape(wrapped, quote=True) + '">Law</a>'
        with patch("tools.search._get_text", return_value=page):
            output = self.invoke("duckduckgo")
        self.assertIn(target, output)
        self.assertEqual(parse_qs(urlparse(_unwrap_ddg_url(wrapped)).query)["q"], [QUERY])

    def test_unrelated_hostname_is_not_a_ddg_redirect(self):
        href = "https://notduckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com"
        self.assertEqual(_unwrap_ddg_url(href), href)

    def test_duplicate_redirect_queries_do_not_hide_a_rewrite(self):
        requests = []

        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                location = "/search?" + urlencode([("q", QUERY), ("q", "反")])
                return httpx.Response(302, headers={"Location": location})
            return httpx.Response(200, text=BING_RESULTS)

        with self.transport(handler):
            output = self.invoke()
        self.assertTrue(output.startswith("Error: [search_query_changed]"), output)

    def test_invalid_api_json_keeps_error_contract(self):
        with self.transport(lambda request: httpx.Response(200, text="not JSON")):
            output = self.invoke("brave")
        self.assertTrue(output.startswith("Error: [search_failed]"), output)

    def test_transport_failures_keep_error_contract(self):
        for failure in (httpx.ReadTimeout("timed out"), httpx.ConnectError("connection failed")):
            with self.subTest(failure=failure):
                def handler(request):
                    raise failure
                with self.transport(handler):
                    output = self.invoke()
                self.assertTrue(output.startswith("Error: [search_failed]"), output)

    def test_redirect_loop_keeps_error_contract(self):
        with self.transport(lambda request: httpx.Response(302, headers={"Location": str(request.url)})):
            output = self.invoke()
        self.assertTrue(output.startswith("Error: [search_failed]"), output)

    def test_http_failure_does_not_claim_query_rewriting(self):
        with self.transport(lambda request: httpx.Response(429, text="captcha")):
            output = self.invoke()
        self.assertTrue(output.startswith("Error: [search_http_error]"), output)
        self.assertNotIn("search_query_changed", output)

    def test_blocked_and_empty_pages_keep_distinct_outcomes(self):
        for provider in ("bing", "duckduckgo"):
            with self.subTest(provider=provider):
                with patch("tools.search._get_text", return_value="<html>captcha</html>"):
                    blocked = self.invoke(provider)
                self.assertTrue(blocked.startswith("Error: [search_blocked]"), blocked)
                with patch("tools.search._get_text", return_value="<html></html>"):
                    empty = self.invoke(provider)
                self.assertIn(f'No results found for "{QUERY}"', empty)


    def test_bing_first_character_dictionary_results_are_diagnostic(self):
        titles = ['反（汉语汉字）_百度百科', '反 fǎn - 汉典', '反的意思,反的解释-汉语国学']
        page = '<input name="q" value="' + QUERY + '">' + ''.join(
            f'<li class="b_algo"><h2><a href="https://example.com/{i}">{title}</a></h2><p>释义</p></li>'
            for i, title in enumerate(titles))
        with patch('tools.search._get_text', return_value=page):
            output = self.invoke()
        self.assertTrue(output.startswith('Error: [search_results_unrelated]'), output)
        self.assertNotIn('https://example.com', output)
        self.assertIn('did not shorten', output)

    def test_dictionary_guard_does_not_infer_general_relevance(self):
        from tools.search import _check_bing_dictionary_degradation
        entries = [{'title': title, 'snippet': ''} for title in
                   ['反（汉语汉字）_百度百科', '反 fǎn - 汉典', '反的意思-汉语国学']]
        for query in ['反', '反 拼音', '反垄断法 汉字', 'English legal question']:
            _check_bing_dictionary_degradation(query, entries)
        _check_bing_dictionary_degradation(QUERY, entries[:2])
        _check_bing_dictionary_degradation(QUERY, entries + [{'title': '反垄断法原文', 'snippet': ''}])
        _check_bing_dictionary_degradation(QUERY, [{'title': 'Other results', 'snippet': ''}] * 3)


if __name__ == "__main__":
    unittest.main()
