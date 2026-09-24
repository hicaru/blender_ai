"""OpenAI-compatible HTTP client for LLM providers.
# mypy: ignore-errors

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

__all__ = (
    "PROVIDERS",
    "ProviderCancelled",
    "ProviderError",
    "auto_vision_model",
    "chat_completions",
    "image_part",
    "list_models",
    "load_vision_state",
    "mark_no_vision",
    "model_belongs",
    "supports_vision",
    "text_part",
    "vision_state",
)


PROVIDERS = {
    "zai": {
        "label": "Z.ai (GLM)",
        "base_url": "https://api.z.ai/api/paas/v4",
        "default_model": "glm-4.6",
        "vision_model": "glm-4.5v",  # auto captioner when the main model is text-only
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-flash",
        "vision_model": "deepseek-flash",  # api-docs.deepseek.com/guides/vision
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


# ------------------------------------------------------------- vision

# Vision capability, most reliable source first:
# 1. _NO_VISION  — learned at runtime: the provider rejected an image;
# 2. _VISION_MODELS — /models metadata (OpenRouter input_modalities) and
#    ids confirmed by provider docs (_DOC_VISION, verified 2026-09);
# 3. an id heuristic for unknown models.
# 1 and 2 are persisted by prefs (models_cache JSON) across restarts.
_DOC_VISION = {
    "deepseek": frozenset({"deepseek-flash"}),   # deepseek-v4-pro: no vision
    "zai": frozenset({"glm-4.5v", "glm-4.6v"}),
}

# /models lists may contain foreign entries (z.ai, for example, lists a
# ``deepseek-flash`` proxy). Only these id prefixes count as the provider's
# own model family; providers without an entry are kept verbatim.
_MODEL_FILTERS = {"zai": "glm"}
_VISION_ID_HINTS = frozenset({
    "glm-4.5v", "glm-4.6v", "glm-4v", "gpt-4o", "gpt-4.1", "gpt-5",
    "claude", "gemini", "llama-3.2-90b-vision", "qwen-vl", "pixtral",
    "vlm", "vision",
})
_VISION_MODELS = {}  # provider_id -> frozenset(ids with image input)
_NO_VISION = {}      # provider_id -> frozenset(ids that rejected images)


def text_part(text):
    """OpenAI-compatible text content part."""
    return {"type": "text", "text": text}


def image_part(data_url):
    """OpenAI-compatible image content part (base64 data URL)."""
    return {"type": "image_url", "image_url": {"url": data_url}}


def _vision_from_id(model):
    low = model.lower()
    return any(hint in low for hint in _VISION_ID_HINTS)


def model_belongs(provider_id, model):
    """True if ``model`` is in the provider's own model family.

    Providers without a family filter (OpenRouter) accept any id.
    """
    prefix = _MODEL_FILTERS.get(provider_id, "")
    return not prefix or str(model).lower().startswith(prefix)


def remember_vision_models(provider_id, model_rows):
    """Cache vision capability from /models rows (OpenRouter: architecture)."""
    ids = set()
    for row in model_rows:
        if not isinstance(row, dict):
            continue
        mid = row.get("id")
        if not mid:
            continue
        arch = row.get("architecture") or {}
        modalities = arch.get("input_modalities") or []
        if "image" in modalities:
            ids.add(str(mid))
    if ids:
        _VISION_MODELS[provider_id] = frozenset(ids)


def mark_no_vision(provider_id, model):
    """The provider rejected an image for this model: never send it one again."""
    _NO_VISION[provider_id] = _NO_VISION.get(provider_id, frozenset()) | {model}


def vision_state():
    """JSON-safe snapshot of learned capabilities (persisted by prefs)."""
    return {"yes": {p: sorted(ids) for p, ids in _VISION_MODELS.items()},
            "no": {p: sorted(ids) for p, ids in _NO_VISION.items()}}


def load_vision_state(state):
    """Restore :func:`vision_state` output (bad shapes are ignored)."""
    if not isinstance(state, dict):
        return
    for key, target in (("yes", _VISION_MODELS), ("no", _NO_VISION)):
        rows = state.get(key)
        if not isinstance(rows, dict):
            continue
        for provider_id, ids in rows.items():
            if isinstance(ids, list):
                target[str(provider_id)] = frozenset(str(i) for i in ids)


def supports_vision(provider_id, model):
    """Whether this provider+model accepts image content parts."""
    if model in _NO_VISION.get(provider_id, ()):
        return False
    if model in _VISION_MODELS.get(provider_id, ()):
        return True
    if model in _DOC_VISION.get(provider_id, ()):
        return True
    return _vision_from_id(model)


_VISION_PICK_HINTS = ("glm-4.6v", "glm-4.5v", "4v", "pixtral", "vl",
                      "vision", "vlm")


def auto_vision_model(provider_id):
    """Zero-config captioner for a provider.

    Prefers a model already known to accept images (from the ``/models``
    metadata cached by :func:`remember_vision_models`), then a static
    ``vision_model`` from PROVIDERS. Returns ``None`` when the provider
    serves no known vision model. Models that rejected images are skipped.
    """
    bad = _NO_VISION.get(provider_id, frozenset())
    ids = (_VISION_MODELS.get(provider_id, frozenset())
           | _DOC_VISION.get(provider_id, frozenset())) - bad

    def _rank(mid):
        low = mid.lower()
        hint = next((i for i, h in enumerate(_VISION_PICK_HINTS) if h in low),
                    len(_VISION_PICK_HINTS))
        return (hint, mid)

    if ids:
        return min(ids, key=_rank)
    model = (PROVIDERS.get(provider_id) or {}).get("vision_model")
    return None if model in bad else model


def _close_response(resp):
    """Best-effort close (fakes in tests may lack .close)."""
    close = getattr(resp, "close", None)
    if close is not None:
        import contextlib

        with contextlib.suppress(Exception):  # close is best-effort
            close()


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
        raise ProviderError("Network error talking to %s: %s" % (provider["label"], exc)) from exc

    if response.status_code != 200:
        raise ProviderError(
            "%s returned HTTP %d: %s"
            % (provider["label"], response.status_code, response.text[:300])
        )

    try:
        data = response.json()["data"]
        rows = [item for item in data if item.get("id")]
        kept = [item for item in rows if model_belongs(provider_id, item["id"])]
        rows = kept or rows  # family renamed upstream: keep the raw list
        ids = sorted(str(item["id"]) for item in rows)
        remember_vision_models(provider_id, rows)
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError(
            "Unexpected model list shape from %s: %s" % (provider["label"], exc)
        ) from exc

    if not ids:
        raise ProviderError("%s returned an empty model list." % provider["label"])
    return ids


def _apply_reasoning(payload, provider_id, thinking, effort):  # noqa: PLR0912 — provider-specific payloads
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


def _consume_stream(response, on_delta, stop_event=None):  # noqa: PLR0912, PLR0915 — SSE accumulation
    """Read an OpenAI-style SSE chat stream and assemble the message.

    Recognizes ``reasoning_content`` (DeepSeek, Z.ai) and ``reasoning``
    (OpenRouter) reasoning deltas plus incremental tool_calls. A set
    ``stop_event`` (user pressed Stop) raises :class:`ProviderCancelled`
    immediately, closing the connection. Returns ``(message, usage,
    finish_reason)``; ``finish_reason`` is the last non-empty value seen
    ("stop", "length", "tool_calls", …) and "" when the stream sent none.
    """
    content_parts = []
    reasoning_parts = []
    tool_calls = {}
    usage = {}
    finish_reason = ""
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
        chunk_finish = choices[0].get("finish_reason")
        if chunk_finish:
            finish_reason = str(chunk_finish)
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
        calls = [tool_calls[key] for key in sorted(tool_calls)]
        for i, call in enumerate(calls):
            if not call["id"]:
                # Some relays stream tool_call deltas with no id; an empty
                # id makes history.reconcile() drop the tool result, leaving
                # an unanswered tool_call. Same deterministic fallback as
                # _normalize_message().
                call["id"] = "call_%d" % (i + 1)
        message["tool_calls"] = calls
    return message, usage, finish_reason


def _normalize_message(raw):
    """Force one assistant-message shape regardless of provider path.

    Streaming builds this shape while consuming deltas; the non-streaming
    branch trusts the relay, which sometimes sends ``content`` as ``None``
    or a list of parts, or ``tool_calls[].function.arguments`` as a dict —
    all of which break Blender property assignment later and kill the
    polling timer (permanent busy spinner) unless normalized here.
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


def chat_completions(  # noqa: PLR0912, PLR0915
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

    Returns ``{"message": <assistant message dict>, "usage": <dict>,
    "finish_reason": <str>}`` ("stop", "length", "tool_calls", …; ""
    when the provider sent none — "length" means the output budget was
    exhausted mid-answer and the caller should auto-continue).
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
            ) from exc

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
            message, usage, finish_reason = _consume_stream(response, on_delta, stop_event)
            return {"message": message, "usage": usage, "finish_reason": finish_reason}
        data = response.json()
        message = _normalize_message(data["choices"][0]["message"])
        usage = data.get("usage", {})
        finish_reason = str(data["choices"][0].get("finish_reason") or "")
    except ProviderError:
        raise
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            "Unexpected response shape from %s: %s" % (provider["label"], exc)
        ) from exc
    finally:
        _close_response(response)

    return {"message": message, "usage": usage, "finish_reason": finish_reason}
