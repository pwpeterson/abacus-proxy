from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import httpx
import pytest
from httpx import ASGITransport as RealASGITransport
from httpx import AsyncClient as RealAsyncClient

import src.app_fastapi as app_fastapi_mod
import src.mcp.tools as mcp_tools_mod
from src.app_fastapi import create_app
from src.models.config import AppConfig


@dataclass
class _Call:
    method: str
    url: str
    headers: dict[str, str] | None
    json: dict[str, Any] | None


def _make_httpx_response(*, method: str, url: str, status_code: int, json_data: Any) -> httpx.Response:
    req = httpx.Request(method, url)
    return httpx.Response(status_code=status_code, json=json_data, request=req)


def _fake_async_client_factory(
    *,
    calls: list[_Call],
    handler: Callable[[str, str, dict[str, str] | None, dict[str, Any] | None], httpx.Response],
):
    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            _ = (exc_type, exc, tb)
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
            calls.append(_Call(method="GET", url=str(url), headers=headers, json=None))
            return handler("GET", str(url), headers, None)

        async def post(
            self, url: str, headers: dict[str, str] | None = None, json: dict[str, Any] | None = None
        ) -> httpx.Response:
            calls.append(_Call(method="POST", url=str(url), headers=headers, json=json))
            return handler("POST", str(url), headers, json)

    return FakeAsyncClient


def _base_config(*, routellm_api_base_url: str = "http://upstream.local/v1") -> AppConfig:
    # Keep memory features disabled in integration tests to avoid importing mem0.
    return AppConfig(
        routellm_api_base_url=routellm_api_base_url,
        routellm_model_id="route-llm",
        route_default_target_model="gpt-5.2",
        model_aliases={"route-llm": "gpt-5.2"},
        enable_memory=False,
        enable_rules_memory=False,
        enable_code_jit_context=False,
        auto_index_rules_on_startup=False,
        enable_context_management=False,
        prompt_guard_enabled=False,
        enable_mcp=False,
    )


def _install_dummy_mem0(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyMemoryService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)
            self.is_enabled = False

    class DummyContextManager:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

    monkeypatch.setattr(app_fastapi_mod, "MemoryService", DummyMemoryService)
    monkeypatch.setattr(app_fastapi_mod, "ContextManager", DummyContextManager)


@pytest.mark.asyncio
async def test_chat_completions_model_alias_and_tool_schema_sanitization(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[_Call] = []

    def handler(method: str, url: str, _headers: Any, json_payload: dict[str, Any] | None) -> httpx.Response:
        assert method == "POST"
        assert url.endswith("/chat/completions")
        assert json_payload is not None

        # Model aliasing must have happened before upstream call
        assert json_payload["model"] == "gpt-5.2"

        # Tool schema sanitization should have removed propertyNames and enforced additionalProperties=False
        tools = json_payload.get("tools")
        assert isinstance(tools, list) and tools
        fn = tools[0]["function"]
        params = fn["parameters"]
        assert "propertyNames" not in params
        assert params.get("type") == "object"
        assert params.get("additionalProperties") is False
        assert isinstance(params.get("properties"), dict)

        upstream_json = {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": json_payload["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }
        return _make_httpx_response(method="POST", url=url, status_code=200, json_data=upstream_json)

    FakeAsyncClient = _fake_async_client_factory(calls=calls, handler=handler)
    monkeypatch.setattr(app_fastapi_mod.httpx, "AsyncClient", FakeAsyncClient)
    _install_dummy_mem0(monkeypatch)

    app = create_app(_base_config())
    transport = RealASGITransport(app=app)
    async with RealAsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "model": "route-llm",
            "messages": [
                {
                    "role": "system",
                    "content": "You are a helpful assistant.\n\nMCP SERVERS\n\n(placeholder)\n\n====",
                },
                {"role": "user", "content": "hi"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "t",
                        "parameters": {
                            "type": "object",
                            "propertyNames": {"pattern": ".*"},
                            "properties": {"x": "string"},
                        },
                    },
                }
            ],
        }
        r = await client.post("/v1/chat/completions", json=payload)
        assert r.status_code == 200

    assert len(calls) == 1
    assert calls[0].json is not None
    assert calls[0].json["model"] == "gpt-5.2"


@pytest.mark.asyncio
async def test_chat_completions_rewrites_text_only_to_attempt_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    # Legacy proxy-side forced attempt_completion rewrite was removed.
    # Keep this test as a no-op placeholder so historical behavior is documented.
    pytest.skip("proxy-side text-only->attempt_completion rewrite was removed")


@pytest.mark.asyncio
async def test_chat_completions_does_not_rewrite_text_only_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[_Call] = []

    def handler(method: str, url: str, _headers: Any, json_payload: dict[str, Any] | None) -> httpx.Response:
        assert method == "POST"
        upstream_json = {
            "id": "chatcmpl-2b",
            "object": "chat.completion",
            "created": 0,
            "model": (json_payload or {}).get("model", "gpt-5.2"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "plain text reply"},
                    "finish_reason": "stop",
                }
            ],
        }
        return _make_httpx_response(method="POST", url=url, status_code=200, json_data=upstream_json)

    FakeAsyncClient = _fake_async_client_factory(calls=calls, handler=handler)
    monkeypatch.setattr(app_fastapi_mod.httpx, "AsyncClient", FakeAsyncClient)
    _install_dummy_mem0(monkeypatch)

    # Default: proxy does not force attempt_completion rewrite
    app = create_app(_base_config())
    transport = RealASGITransport(app=app)
    async with RealAsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-5.2",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "hello"},
                ],
            },
        )
        data = r.json()

    msg = data["choices"][0]["message"]
    assert msg.get("content") == "plain text reply"
    assert not msg.get("tool_calls")
    assert data["choices"][0]["finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_chat_completions_stream_true_translates_to_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[_Call] = []

    def handler(method: str, url: str, _headers: Any, json_payload: dict[str, Any] | None) -> httpx.Response:
        assert method == "POST"
        assert json_payload is not None
        # In the stream path, abacus-proxy forces upstream stream=false
        assert json_payload.get("stream") is False
        upstream_json = {
            "id": "chatcmpl-3",
            "object": "chat.completion",
            "created": 0,
            "model": json_payload["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "streamed text"},
                    "finish_reason": "stop",
                }
            ],
        }
        return _make_httpx_response(method="POST", url=url, status_code=200, json_data=upstream_json)

    FakeAsyncClient = _fake_async_client_factory(calls=calls, handler=handler)
    monkeypatch.setattr(app_fastapi_mod.httpx, "AsyncClient", FakeAsyncClient)
    _install_dummy_mem0(monkeypatch)

    app = create_app(_base_config())
    transport = RealASGITransport(app=app)
    async with RealAsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-5.2", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = (await r.aread()).decode("utf-8")
        assert "data:" in body
        assert "[DONE]" in body


@pytest.mark.asyncio
async def test_models_endpoint_normalizes_base_url_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[_Call] = []

    def handler(method: str, url: str, _headers: Any, _json_payload: dict[str, Any] | None) -> httpx.Response:
        assert method == "GET"
        # Should have stripped backslashes in the configured api base url
        assert url.startswith("https://example.com/v1/models")
        upstream_json = {"object": "list", "data": [{"id": "gpt-5.2"}]}
        return _make_httpx_response(method="GET", url=url, status_code=200, json_data=upstream_json)

    FakeAsyncClient = _fake_async_client_factory(calls=calls, handler=handler)
    monkeypatch.setattr(app_fastapi_mod.httpx, "AsyncClient", FakeAsyncClient)
    _install_dummy_mem0(monkeypatch)

    cfg = _base_config(routellm_api_base_url=r"https:\/\/example.com\/v1")
    app = create_app(cfg)
    transport = RealASGITransport(app=app)

    async with RealAsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.get("/v1/models")
        assert r1.status_code == 200
        r2 = await client.get("/v1/models")
        assert r2.status_code == 200

    # Second call should hit cache (only one upstream GET)
    assert len([c for c in calls if c.method == "GET"]) == 1


@pytest.mark.asyncio
async def test_models_endpoint_enrich_value_index_on_demand_without_polluting_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[_Call] = []

    upstream_json = {
        "object": "list",
        "data": [
            # Router model should be excluded from scoring by default
            {
                "id": "route-llm",
                "model_type": "text_generation",
                "context_length": 200000,
                "max_completion_tokens": 64000,
                "input_token_rate": "0.000003",
                "output_token_rate": "0.000015",
            },
            # Eligible for all use-cases (>=256k context for condensation)
            {
                "id": "gpt-5-nano",
                "model_type": "text_generation",
                "context_length": 272000,
                "max_completion_tokens": 16384,
                "input_token_rate": "0.00000005",
                "output_token_rate": "0.0000004",
            },
            # Eligible for coding+architecting, but not condensation (context < 256k)
            {
                "id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
                "model_type": "text_generation",
                "context_length": 131072,
                "max_completion_tokens": 8192,
                "input_token_rate": "0.00000002",
                "output_token_rate": "0.00000005",
            },
            # Fails gates due to short context and low max_out
            {
                "id": "tiny-model",
                "model_type": "text_generation",
                "context_length": 64000,
                "max_completion_tokens": 256,
                "input_token_rate": "0.00000001",
                "output_token_rate": "0.00000001",
            },
        ],
    }

    def handler(method: str, url: str, _headers: Any, _json_payload: dict[str, Any] | None) -> httpx.Response:
        assert method == "GET"
        assert url.endswith("/models")
        return _make_httpx_response(method="GET", url=url, status_code=200, json_data=upstream_json)

    FakeAsyncClient = _fake_async_client_factory(calls=calls, handler=handler)
    monkeypatch.setattr(app_fastapi_mod.httpx, "AsyncClient", FakeAsyncClient)
    _install_dummy_mem0(monkeypatch)

    cfg = _base_config()
    cfg.enable_model_value_index = True
    # Set a non-default prior to ensure it is reflected in output metadata.
    cfg.model_value_index_family_priors = {"openai": 1.05}

    app = create_app(cfg)
    transport = RealASGITransport(app=app)

    async with RealAsyncClient(transport=transport, base_url="http://test") as client:
        # 1) Enriched response (should call upstream once)
        r1 = await client.get("/v1/models?enrich=value_index")
        assert r1.status_code == 200
        d1 = r1.json()
        assert "abacus_proxy_meta" in d1
        assert d1["abacus_proxy_meta"]["version"] == 1
        assert d1["abacus_proxy_meta"]["family_quality_priors"].get("openai") == 1.05

        # Router model present but not scored
        route_entry = next(m for m in d1["data"] if m["id"] == "route-llm")
        assert "abacus_proxy" not in route_entry

        nano_entry = next(m for m in d1["data"] if m["id"] == "gpt-5-nano")
        assert nano_entry["abacus_proxy"]["use_cases"]["coding"]["eligible"] is True
        assert nano_entry["abacus_proxy"]["use_cases"]["condensation"]["eligible"] is True
        assert nano_entry["abacus_proxy"]["use_cases"]["coding"]["index"] > 0

        llama_entry = next(m for m in d1["data"] if m["id"].startswith("meta-llama/"))
        assert llama_entry["abacus_proxy"]["use_cases"]["coding"]["eligible"] is True
        assert llama_entry["abacus_proxy"]["use_cases"]["condensation"]["eligible"] is False
        assert "context_length<256000" in llama_entry["abacus_proxy"]["use_cases"]["condensation"]["notes"][0]

        tiny_entry = next(m for m in d1["data"] if m["id"] == "tiny-model")
        assert tiny_entry["abacus_proxy"]["use_cases"]["coding"]["eligible"] is False
        assert tiny_entry["abacus_proxy"]["use_cases"]["coding"]["index"] == 0.0

        # 2) Non-enriched response should come from raw cache (no abacus_proxy keys)
        r2 = await client.get("/v1/models")
        assert r2.status_code == 200
        d2 = r2.json()
        assert "abacus_proxy_meta" not in d2
        assert all("abacus_proxy" not in m for m in d2.get("data", []) if isinstance(m, dict))

        # 3) Enriched again should NOT call upstream again (still one upstream GET)
        r3 = await client.get("/v1/models?enrich=value_index")
        assert r3.status_code == 200

    assert len([c for c in calls if c.method == "GET"]) == 1


@pytest.mark.asyncio
async def test_legacy_completions_translates_prompt_and_calls_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[_Call] = []

    def handler(method: str, url: str, _headers: Any, json_payload: dict[str, Any] | None) -> httpx.Response:
        assert method == "POST"
        assert url.endswith("/chat/completions")
        assert json_payload is not None
        # Legacy prompt should become a single user message
        assert json_payload["messages"][0]["role"] == "user"
        assert "a\nb" in json_payload["messages"][0]["content"]
        upstream_json = {
            "id": "chatcmpl-legacy",
            "object": "chat.completion",
            "created": 0,
            "model": json_payload["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
        }
        return _make_httpx_response(method="POST", url=url, status_code=200, json_data=upstream_json)

    FakeAsyncClient = _fake_async_client_factory(calls=calls, handler=handler)
    monkeypatch.setattr(app_fastapi_mod.httpx, "AsyncClient", FakeAsyncClient)
    _install_dummy_mem0(monkeypatch)

    app = create_app(_base_config())
    transport = RealASGITransport(app=app)

    async with RealAsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/v1/completions",
            json={"model": "route-llm", "prompt": ["a", "b"], "max_tokens": 10},
        )
        assert r.status_code == 200

    assert len([c for c in calls if c.method == "POST"]) == 1


@pytest.mark.asyncio
async def test_mcp_send_chat_request_posts_to_upstream_with_forced_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[_Call] = []

    def handler(method: str, url: str, _headers: Any, json_payload: dict[str, Any] | None) -> httpx.Response:
        assert method == "POST"
        assert url.endswith("/chat/completions")
        assert json_payload is not None
        assert json_payload["model"] == "route-llm"
        upstream_json = {"id": "x", "choices": []}
        return _make_httpx_response(method="POST", url=url, status_code=200, json_data=upstream_json)

    FakeAsyncClient = _fake_async_client_factory(calls=calls, handler=handler)
    monkeypatch.setattr(mcp_tools_mod.httpx, "AsyncClient", FakeAsyncClient)

    cfg = _base_config()
    # MCP path reads these toggles; keep prompt guard off so request is stable.
    cfg.prompt_guard_enabled = False

    app_resources = {"config": cfg, "memory_service": None, "context_manager": None}
    result = await mcp_tools_mod.send_chat_request(
        app_resources,
        {
            "model": "client-model",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.1,
            "presence_penalty": 0.0,
        },
    )

    assert result["status"] == "success"
    assert len(calls) == 1
    assert calls[0].json is not None
    assert calls[0].json["model"] == "route-llm"
