"""Provider client tests: request form (url/headers/body/tools) with a
monkeypatched requests.post, error mapping, and the verified endpoints."""

import json as _json
import unittest

import _common

providers = _common.load_module("providers")

_CAPTURED = {}


def _install_post(status_code=200, body=None):
    requests = providers.requests
    _CAPTURED.clear()

    def fake_post(url, headers=None, json=None, timeout=None):
        _CAPTURED.update(url=url, headers=headers, json=json, timeout=timeout)
        response = type("R", (), {})()
        response.status_code = status_code
        response.text = _json.dumps(body or {})
        response._body = body if body is not None else {}
        response.json = lambda: response._body
        return response

    requests.post = fake_post


class TestEndpoints(unittest.TestCase):
    """Step-0 facts: verified against official docs, do not drift."""

    def test_zai_endpoint_and_model(self):
        self.assertEqual(
            providers.PROVIDERS["zai"]["base_url"],
            "https://api.z.ai/api/paas/v4",
        )
        self.assertEqual(providers.PROVIDERS["zai"]["default_model"], "glm-4.6")

    def test_deepseek_endpoint_and_model(self):
        self.assertEqual(
            providers.PROVIDERS["deepseek"]["base_url"],
            "https://api.deepseek.com",
        )
        self.assertEqual(
            providers.PROVIDERS["deepseek"]["default_model"], "deepseek-flash"
        )

    def test_openrouter_endpoint(self):
        self.assertEqual(
            providers.PROVIDERS["openrouter"]["base_url"],
            "https://openrouter.ai/api/v1",
        )


class TestChatCompletions(unittest.TestCase):
    def test_request_form(self):
        _install_post(body={"choices": [{"message": {"role": "assistant",
                                                     "content": "hi"}}],
                            "usage": {"total_tokens": 5}})
        tools = [{"type": "function", "function": {"name": "t", "description": "",
                                                   "parameters": {}}}]
        result = providers.chat_completions(
            "zai", "key-123", "glm-4.6",
            [{"role": "user", "content": "hello"}],
            tools=tools, temperature=0.2, timeout=30,
        )
        self.assertEqual(_CAPTURED["url"],
                         "https://api.z.ai/api/paas/v4/chat/completions")
        self.assertEqual(_CAPTURED["headers"]["Authorization"], "Bearer key-123")
        self.assertEqual(_CAPTURED["json"]["model"], "glm-4.6")
        self.assertEqual(_CAPTURED["json"]["temperature"], 0.2)
        self.assertEqual(_CAPTURED["json"]["tools"], tools)
        self.assertEqual(_CAPTURED["json"]["messages"],
                         [{"role": "user", "content": "hello"}])
        self.assertEqual(result["message"]["content"], "hi")
        self.assertEqual(result["usage"], {"total_tokens": 5})

    def test_no_tools_field_when_none(self):
        _install_post(body={"choices": [{"message": {"role": "assistant",
                                                     "content": ""}}]})
        providers.chat_completions("deepseek", "k", "deepseek-flash", [])
        self.assertNotIn("tools", _CAPTURED["json"])
        self.assertEqual(_CAPTURED["url"],
                         "https://api.deepseek.com/chat/completions")

    def test_missing_key_raises_before_http(self):
        _install_post()
        with self.assertRaises(providers.ProviderError):
            providers.chat_completions("zai", "", "glm-4.6", [])
        self.assertNotIn("url", _CAPTURED)  # no HTTP call attempted

    def test_unknown_provider(self):
        with self.assertRaises(providers.ProviderError):
            providers.chat_completions("nope", "k", "m", [])

    def test_http_error_becomes_provider_error(self):
        _install_post(status_code=401, body={"error": "bad key"})
        with self.assertRaises(providers.ProviderError) as ctx:
            providers.chat_completions("zai", "k", "glm-4.6", [])
        self.assertIn("401", str(ctx.exception))

    def test_bad_shape_becomes_provider_error(self):
        _install_post(body={"unexpected": True})
        with self.assertRaises(providers.ProviderError):
            providers.chat_completions("zai", "k", "glm-4.6", [])


class TestListModels(unittest.TestCase):
    def _install_get(self, status_code=200, body=None):
        requests = providers.requests

        def fake_get(url, headers=None, timeout=None):
            _CAPTURED.update(url=url, headers=headers, timeout=timeout)
            response = type("R", (), {})()
            response.status_code = status_code
            response.text = _json.dumps(body or {})
            response._body = body if body is not None else {}
            response.json = lambda: response._body
            return response

        requests.get = fake_get
        _CAPTURED.clear()

    def test_request_form_and_parse(self):
        self._install_get(body={"data": [{"id": "model-b"}, {"id": "model-a"},
                                         {"object": "no-id-here"}]})
        ids = providers.list_models("deepseek", "key-1")
        self.assertEqual(_CAPTURED["url"], "https://api.deepseek.com/models")
        self.assertEqual(_CAPTURED["headers"]["Authorization"], "Bearer key-1")
        self.assertEqual(ids, ["model-a", "model-b"])  # sorted, no-id skipped

    def test_openrouter_keyless(self):
        self._install_get(body={"data": [{"id": "openai/gpt-4o-mini"}]})
        ids = providers.list_models("openrouter", "")
        self.assertEqual(_CAPTURED["url"], "https://openrouter.ai/api/v1/models")
        self.assertNotIn("Authorization", _CAPTURED["headers"])
        self.assertEqual(ids, ["openai/gpt-4o-mini"])

    def test_missing_key_non_openrouter(self):
        self._install_get()
        with self.assertRaises(providers.ProviderError):
            providers.list_models("zai", "")
        self.assertNotIn("url", _CAPTURED)

    def test_http_error(self):
        self._install_get(status_code=401, body={"error": "nope"})
        with self.assertRaises(providers.ProviderError):
            providers.list_models("deepseek", "k")

    def test_bad_shape(self):
        self._install_get(body={"models": ["x"]})
        with self.assertRaises(providers.ProviderError):
            providers.list_models("deepseek", "k")


if __name__ == "__main__":
    unittest.main()
