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
import time

import requests

__all__ = ("PROVIDERS", "ProviderError", "ProviderCancelled",
           "chat_completions", "list_models")


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


class ProviderCancelled(ProviderError):
    """Raised when the user stops the run while the stream is in flight."""


def _close_response(resp):
    """Best-effort close (fakes in tests may lack .close)."""
    close = getattr(resp, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # noqa: BLE001 — cleanup must never mask the real error
            pass


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


def _consume_stream(response, on_delta, stop_event=None):
    """Read an OpenAI-style SSE chat stream and assemble the message.

    Recognizes ``reasoning_content`` (DeepSeek, Z.ai) and ``reasoning``
    (OpenRouter) reasoning deltas plus incremental tool_calls. A set
    ``stop_event`` (user pressed Stop) raises :class:`ProviderCancelled`
    immediately, closing the connection. Returns ``(message, usage)``.
    """
    content_parts = []
    reasoning_parts = []
    tool_calls = {}
    usage = {}
    for raw in response.iter_lines():
        if stop_event is not None and stop_event.is_set():
            raise ProviderCancelled("stream stopped by user")
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
        usage_chunk = event.get("usage")
        if isinstance(usage_chunk, dict) and usage_chunk:
            # final chunk, present because of stream_options.include_usage
            usage = usage_chunk
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
    return message, usage


def _normalize_message(raw):
    """Force one assistant-message shape regardless of provider path.

    Streaming builds this shape while consuming deltas; the non-streaming
    branch trusts the relay, which sometimes sends ``content`` as ``None``
    or a list of parts, or ``tool_calls[].function.arguments`` as a dict —
    all of which break Blender property assignment later and used to kill
    the polling timer (permanent busy spinner).
    """
    raw = raw if isinstance(raw, dict) else {}
    content = raw.get("content")
    if isinstance(content, list):  # parts: [{"type": "text", "text": ...}]
        chunks = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        content = "".join(chunks)
    elif not isinstance(content, str):
        content = "" if content is None else str(content)

    tool_calls = []
    for call in raw.get("tool_calls") or ():
        if not isinstance(call, dict):
            continue
        function = call.get("function")
        function = function if isinstance(function, dict) else {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments or {})
            except (TypeError, ValueError):
                arguments = "{}"
        tool_calls.append({
            "id": call.get("id") or "call_%d" % (len(tool_calls) + 1),
            "type": "function",
            "function": {"name": function.get("name") or "",
                         "arguments": arguments},
        })

    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    reasoning = raw.get("reasoning_content") or raw.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        message["reasoning_content"] = reasoning
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
    stop_event=None,
    retries=2,
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
    mapped per provider (unsupported levels are capped). A set
    ``stop_event`` (user pressed Stop) aborts the stream with
    :class:`ProviderCancelled`; transient network errors and HTTP
    429/5xx are retried up to ``retries`` times with linear backoff.

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
        # the final chunk then carries token usage
        payload["stream_options"] = {"include_usage": True}

    attempt = 0
    while True:
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=timeout,
                stream=stream,
            )
        except requests.RequestException as exc:
            if attempt < retries:
                attempt += 1
                time.sleep(1.5 * attempt)
                continue
            raise ProviderError(
                "Network error talking to %s: %s" % (provider["label"], exc)
            )

        if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            # transient: retry with linear backoff before giving up
            attempt += 1
            _close_response(response)
            time.sleep(1.5 * attempt)
            continue

        if response.status_code != 200:
            _close_response(response)
            raise ProviderError(
                "%s returned HTTP %d: %s"
                % (provider["label"], response.status_code, response.text[:300])
            )
        break

    try:
        if stream:
            message, usage = _consume_stream(response, on_delta, stop_event)
            return {"message": message, "usage": usage}
        data = response.json()
        message = _normalize_message(data["choices"][0]["message"])
        usage = data.get("usage", {})
    except ProviderError:
        raise
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Unexpected response shape from %s: %s" % (provider["label"], exc))
    finally:
        _close_response(response)

    return {"message": message, "usage": usage}
