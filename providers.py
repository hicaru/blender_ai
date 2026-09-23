"""OpenAI-compatible HTTP client for LLM providers.

The single place in the addon that knows provider URLs and model ids.
Verified against official docs (2026-02):
- z.ai:       POST {base_url}/chat/completions, model ``glm-4.6``
              (docs.z.ai/guides/llm/glm-4.6, curl examples)
- DeepSeek:   POST {base_url}/chat/completions, model ``deepseek-flash``
              (api-docs.deepseek.com: base_url ``https://api.deepseek.com``;
              legacy ``deepseek-chat`` is retired)
- OpenRouter: POST {base_url}/chat/completions, model is a free-form user
              field like ``openai/gpt-4o-mini`` (openrouter.ai/docs)

If a provider endpoint changes, only the PROVIDERS table needs editing.

Contingency (documented, not used in MVP): if the selected OpenRouter model
lacks native tool-calling, a JSON-in-text protocol fallback would be needed;
this branch is intentionally not implemented.
"""

import json

import requests

__all__ = ("PROVIDERS", "ProviderError", "chat_completions", "list_models")


PROVIDERS = {
    "zai": {
        "label": "Z.ai (GLM)",
        "base_url": "https://api.z.ai/api/paas/v4",
        "default_model": "glm-4.6",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "",
    },
}


class ProviderError(RuntimeError):
    """Raised for any provider/network/request-shape failure."""


def list_models(provider_id, api_key, timeout=20):
    """GET ``{base_url}/models`` and return a sorted list of model ids.

    OpenAI-compatible shape: ``{"data": [{"id": ...}, ...]}``.
    OpenRouter's list is public; z.ai and DeepSeek require the API key.
    Raises :class:`ProviderError` on any failure.
    """
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise ProviderError("Unknown provider: %r" % provider_id)
    if not api_key and provider_id != "openrouter":
        raise ProviderError(
            "No API key for %s. Set it in Add-ons preferences." % provider["label"]
        )

    url = provider["base_url"].rstrip("/") + "/models"
    headers = {"Authorization": "Bearer %s" % api_key} if api_key else {}
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError("Network error talking to %s: %s" % (provider["label"], exc))

    if response.status_code != 200:
        raise ProviderError(
            "%s returned HTTP %d: %s"
            % (provider["label"], response.status_code, response.text[:300])
        )

    try:
        data = response.json()["data"]
        ids = sorted(str(item["id"]) for item in data if item.get("id"))
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError("Unexpected model list shape from %s: %s" % (provider["label"], exc))

    if not ids:
        raise ProviderError("%s returned an empty model list." % provider["label"])
    return ids


def chat_completions(
    provider_id,
    api_key,
    model,
    messages,
    tools=None,
    temperature=0.4,
    timeout=90,
    thinking=False,
):
    """POST ``{base_url}/chat/completions`` and return a normalized dict.

    Returns ``{"message": <assistant message dict>, "usage": <dict>}``.
    The message dict contains ``role``, ``content`` and, when the model
    decided to call tools, ``tool_calls`` (list of
    ``{"id", "type": "function", "function": {"name", "arguments"}}``).
    With ``thinking=True`` reasoning is requested (and the message may
    carry ``reasoning_content``). Only z.ai and DeepSeek understand the
    ``thinking`` parameter; OpenRouter rejects nothing but ignores it,
    so it is omitted there.

    Raises :class:`ProviderError` on any failure.
    """
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise ProviderError("Unknown provider: %r" % provider_id)
    if not api_key:
        raise ProviderError(
            "No API key for %s. Set it in Add-ons preferences." % provider["label"]
        )
    if not model:
        raise ProviderError("No model configured for %s." % provider["label"])

    url = provider["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": "Bearer %s" % api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
    if thinking and provider_id in ("zai", "deepseek"):
        payload["thinking"] = {"type": "enabled"}
    elif not thinking and provider_id == "zai":
        # GLM reasons by default; switch it off explicitly to save tokens
        payload["thinking"] = {"type": "disabled"}

    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=timeout
        )
    except requests.RequestException as exc:
        raise ProviderError("Network error talking to %s: %s" % (provider["label"], exc))

    if response.status_code != 200:
        raise ProviderError(
            "%s returned HTTP %d: %s"
            % (provider["label"], response.status_code, response.text[:300])
        )

    try:
        data = response.json()
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Unexpected response shape from %s: %s" % (provider["label"], exc))

    return {"message": message, "usage": data.get("usage", {})}
