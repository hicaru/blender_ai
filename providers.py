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


def _apply_reasoning(payload, provider_id, thinking, effort):
    """Populate thinking / reasoning_effort payload keys per provider.

    ``effort`` (when set) drives everything; empty ``effort`` falls back to
    the legacy boolean ``thinking`` flag. Values per official docs:
    OpenRouter ``reasoning_effort``: xhigh, high, medium, low, minimal,
    none. DeepSeek: ``thinking`` on/off + ``reasoning_effort`` (low /
    medium / high). Z.ai: only ``thinking`` on/off.
    """
    if effort:
        if provider_id == "openrouter":
            if effort == "off":
                payload["reasoning_effort"] = "none"
            elif effort == "max":
                payload["reasoning_effort"] = "xhigh"  # capped at documented max
            elif effort in ("low", "medium", "high", "xhigh"):
                payload["reasoning_effort"] = effort
        elif provider_id == "deepseek":
            if effort == "off":
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["thinking"] = {"type": "enabled"}
                if effort in ("low", "medium", "high"):
                    payload["reasoning_effort"] = effort
                elif effort in ("xhigh", "max"):
                    payload["reasoning_effort"] = "high"  # documented cap
        elif provider_id == "zai":
            payload["thinking"] = {
                "type": "disabled" if effort == "off" else "enabled"}
        return

    # legacy boolean path
    if thinking and provider_id in ("zai", "deepseek"):
        payload["thinking"] = {"type": "enabled"}
    elif not thinking and provider_id == "zai":
        # GLM reasons by default; switch it off explicitly to save tokens
        payload["thinking"] = {"type": "disabled"}


def _consume_stream(response, on_delta):
    """Read an OpenAI-style SSE chat stream and assemble the message.

    Recognizes ``reasoning_content`` (DeepSeek, Z.ai) and ``reasoning``
    (OpenRouter) reasoning deltas plus incremental tool_calls.
    """
    content_parts = []
    reasoning_parts = []
    tool_calls = {}
    for raw in response.iter_lines():
        if not raw:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError:
            continue
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            reasoning_parts.append(reasoning)
            if on_delta:
                on_delta("reasoning", reasoning)
        content = delta.get("content")
        if isinstance(content, str) and content:
            content_parts.append(content)
            if on_delta:
                on_delta("content", content)
        for tc in delta.get("tool_calls") or []:
            index = tc.get("index", 0)
            slot = tool_calls.setdefault(index, {
                "id": "", "type": "function",
                "function": {"name": "", "arguments": ""},
            })
            if tc.get("id"):
                slot["id"] = tc["id"]
            function = tc.get("function") or {}
            if function.get("name"):
                slot["function"]["name"] = function["name"]
            if function.get("arguments"):
                slot["function"]["arguments"] += function["arguments"]

    message = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[key] for key in sorted(tool_calls)]
    return message


def chat_completions(
    provider_id,
    api_key,
    model,
    messages,
    tools=None,
    temperature=0.4,
    timeout=90,
    thinking=False,
    reasoning_effort="",
    stream=False,
    on_delta=None,
    max_tokens=8192,
):
    """POST ``{base_url}/chat/completions`` and return a normalized dict.

    Returns ``{"message": <assistant message dict>, "usage": <dict>}``.
    The message dict contains ``role``, ``content`` and, when the model
    decided to call tools, ``tool_calls`` (list of
    ``{"id", "type": "function", "function": {"name", "arguments"}}``).
    With ``stream=True`` the SSE stream is consumed (reasoning deltas feed
    ``on_delta(kind, text)``) and the message is assembled from chunks;
    this keeps bytes flowing so long reasoning cannot hit the read
    timeout. ``reasoning_effort``: off, low, medium, high, xhigh, max —
    mapped per provider (unsupported levels are capped).

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
    if max_tokens:
        # Reasoning tokens count against the output budget: without an
        # explicit cap a thinking model can return empty content.
        payload["max_tokens"] = int(max_tokens)
    if tools:
        payload["tools"] = tools
    _apply_reasoning(payload, provider_id, thinking, reasoning_effort)
    if stream:
        payload["stream"] = True

    try:
        response = requests.post(
            url, headers=headers, json=payload, timeout=timeout,
            stream=stream,
        )
    except requests.RequestException as exc:
        raise ProviderError("Network error talking to %s: %s" % (provider["label"], exc))

    if response.status_code != 200:
        raise ProviderError(
            "%s returned HTTP %d: %s"
            % (provider["label"], response.status_code, response.text[:300])
        )

    try:
        if stream:
            message = _consume_stream(response, on_delta)
            return {"message": message, "usage": {}}
        data = response.json()
        message = data["choices"][0]["message"]
    except ProviderError:
        raise
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Unexpected response shape from %s: %s" % (provider["label"], exc))

    return {"message": message, "usage": data.get("usage", {})}
