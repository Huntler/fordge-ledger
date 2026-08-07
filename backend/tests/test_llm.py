"""LLM settings, provider wire formats, and the polish endpoint.

The provider calls are exercised against an in-process stub rather than mocked
at the client, so the request bodies and response shapes are real. That is the
part most likely to be wrong: Ollama and LM Studio speak different APIs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.llm import DEFAULT_SYSTEM_PROMPT, normalise_base_url


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    monkeypatch.setenv("FORGE_OLLAMA_URL", "")
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


class StubLlm:
    """Stands in for Ollama or LM Studio, recording what it was sent."""

    def __init__(self, provider: str, *, models: list[str], reply: str = "Tightened."):
        self.provider = provider
        self.models = models
        self.reply = reply
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = {}
        if request.method == "POST":
            import json as _json

            body = _json.loads(request.content or b"{}")
        self.requests.append((path, body))

        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in self.models]})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        if path == "/api/generate":
            return httpx.Response(200, json={"response": self.reply})
        if path == "/v1/chat/completions":
            return httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": self.reply}}]}
            )
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch):
    """Route every httpx call in the app through a stub server."""

    def install(server: StubLlm) -> StubLlm:
        transport = httpx.MockTransport(server.handler)
        original = httpx.AsyncClient.__init__

        def patched(self, *args, **kwargs):
            kwargs["transport"] = transport
            original(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
        return server

    return install


def run_polish(client: TestClient, **body) -> dict:
    """Start a polish run and poll until it settles, as the UI does."""
    import time

    started = client.post("/api/llm/polish", json=body)
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]

    for _ in range(200):
        state = client.get(f"/api/llm/polish/{run_id}").json()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError("polish run never settled")


# ------------------------------------------------------------------ defaults


def test_settings_start_empty_with_a_default_prompt(client: TestClient):
    body = client.get("/api/settings/llm").json()

    assert body["provider"] == "ollama"
    assert body["base_url"] == ""
    assert body["system_prompt"] == DEFAULT_SYSTEM_PROMPT
    assert "MakerWorld" in body["system_prompt"]
    assert body["providers"] == ["ollama", "lmstudio"]
    assert body["default_system_prompt"] == DEFAULT_SYSTEM_PROMPT


def test_environment_seeds_the_first_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORGE_LIBRARY_PATH", str(tmp_path / "library"))
    monkeypatch.setenv("FORGE_DATA_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("FORGE_WATCH_ENABLED", "false")
    monkeypatch.setenv("FORGE_OLLAMA_URL", "http://192.168.1.50:11434")
    monkeypatch.setenv("FORGE_OLLAMA_MODEL", "llama3.1")
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        body = client.get("/api/settings/llm").json()
        assert body["base_url"] == "http://192.168.1.50:11434"
        assert body["model"] == "llama3.1"
    get_settings.cache_clear()


# ------------------------------------------------------------------ storage


def test_settings_are_saved_as_readable_yaml(client: TestClient):
    client.put(
        "/api/settings/llm",
        json={
            "provider": "lmstudio",
            "base_url": "192.168.1.42:1234",
            "model": "qwen2.5-7b-instruct",
            "system_prompt": "Be terse.",
            "temperature": 0.1,
        },
    )

    library = Path(client.get("/api/health").json()["library_path"])
    stored = yaml.safe_load((library / "_shared" / "llm.yaml").read_text())

    assert stored["provider"] == "lmstudio"
    assert stored["base_url"] == "http://192.168.1.42:1234"
    assert stored["system_prompt"] == "Be terse."
    assert stored["temperature"] == 0.1


def test_settings_survive_a_restart(client: TestClient, tmp_path: Path):
    client.put(
        "/api/settings/llm",
        json={"provider": "lmstudio", "base_url": "192.168.1.42:1234", "model": "qwen"},
    )

    # A fresh app over the same library reads the file back.
    with TestClient(create_app()) as second:
        body = second.get("/api/settings/llm").json()
        assert body["base_url"] == "http://192.168.1.42:1234"
        assert body["model"] == "qwen"


def test_a_hand_edited_file_is_picked_up_on_reload(client: TestClient):
    library = Path(client.get("/api/health").json()["library_path"])
    target = library / "_shared" / "llm.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump({"provider": "ollama", "base_url": "http://10.0.0.9:11434"}),
        encoding="utf-8",
    )

    body = client.post("/api/settings/llm/reload").json()
    assert body["base_url"] == "http://10.0.0.9:11434"


def test_an_empty_system_prompt_falls_back_to_the_default(client: TestClient):
    body = client.put("/api/settings/llm", json={"system_prompt": "   "}).json()
    assert body["system_prompt"] == DEFAULT_SYSTEM_PROMPT


def test_unknown_provider_is_rejected(client: TestClient):
    assert client.put("/api/settings/llm", json={"provider": "chatgpt"}).status_code == 422


@pytest.mark.parametrize(
    ("entered", "provider", "expected"),
    [
        ("192.168.1.50", "ollama", "http://192.168.1.50:11434"),
        ("192.168.1.50", "lmstudio", "http://192.168.1.50:1234"),
        ("192.168.1.50:1234", "lmstudio", "http://192.168.1.50:1234"),
        ("http://192.168.1.50:1234/v1", "lmstudio", "http://192.168.1.50:1234"),
        ("http://192.168.1.50:11434/", "ollama", "http://192.168.1.50:11434"),
        ("nas.local:11434", "ollama", "http://nas.local:11434"),
    ],
)
def test_base_url_is_normalised(entered: str, provider: str, expected: str):
    """A LAN address gets typed in a dozen ways; they should all work."""
    assert normalise_base_url(entered, provider) == expected


# ------------------------------------------------------------------ probing


def test_test_button_lists_models_without_saving(client: TestClient, stub):
    stub(StubLlm("ollama", models=["llama3.1", "mistral"]))

    result = client.post(
        "/api/settings/llm/test",
        json={"provider": "ollama", "base_url": "192.168.1.50:11434"},
    ).json()

    assert result["available"] is True
    assert result["models"] == ["llama3.1", "mistral"]
    # Probing must not persist a URL the user has not committed to.
    assert client.get("/api/settings/llm").json()["base_url"] == ""


def test_lmstudio_is_probed_on_its_own_endpoint(client: TestClient, stub):
    server = stub(StubLlm("lmstudio", models=["qwen2.5-7b-instruct"]))

    result = client.post(
        "/api/settings/llm/test",
        json={"provider": "lmstudio", "base_url": "192.168.1.42:1234"},
    ).json()

    assert result["available"] is True
    assert result["models"] == ["qwen2.5-7b-instruct"]
    assert server.requests[0][0] == "/v1/models"


def test_an_unreachable_server_explains_itself(client: TestClient, monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    result = client.post(
        "/api/settings/llm/test",
        json={"provider": "ollama", "base_url": "192.168.1.50:11434"},
    ).json()

    assert result["available"] is False
    assert "192.168.1.50" in result["reason"]
    # The usual cause, named outright.
    assert "OLLAMA_HOST=0.0.0.0" in result["hint"]


def test_localhost_gets_the_container_warning(client: TestClient, monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    result = client.post(
        "/api/settings/llm/test", json={"provider": "ollama", "base_url": "localhost:11434"}
    ).json()

    assert "container itself" in result["hint"]


# ------------------------------------------------------------------- polish


def test_polish_uses_the_ollama_api_and_the_custom_prompt(client: TestClient, stub):
    server = stub(StubLlm("ollama", models=["llama3.1"], reply="A tighter description."))
    client.put(
        "/api/settings/llm",
        json={
            "provider": "ollama",
            "base_url": "192.168.1.50:11434",
            "model": "llama3.1",
            "system_prompt": "Write like a laconic machinist.",
            "temperature": 0.15,
        },
    )

    result = run_polish(client, text="# Tray\n\nA tray.")

    assert result["polished"] == "A tighter description."
    assert result["original"] == "# Tray\n\nA tray."

    path, body = next(r for r in server.requests if r[0] == "/api/generate")
    assert path == "/api/generate"
    assert body["model"] == "llama3.1"
    assert body["system"] == "Write like a laconic machinist."
    assert body["options"]["temperature"] == 0.15
    assert body["stream"] is False


def test_polish_uses_chat_completions_for_lmstudio(client: TestClient, stub):
    server = stub(StubLlm("lmstudio", models=["qwen"], reply="Tighter."))
    client.put(
        "/api/settings/llm",
        json={
            "provider": "lmstudio",
            "base_url": "192.168.1.42:1234",
            "model": "qwen",
            "system_prompt": "Be terse.",
        },
    )

    result = run_polish(client, text="# Tray")

    assert result["polished"] == "Tighter."
    path, body = next(r for r in server.requests if r[0] == "/v1/chat/completions")
    assert path == "/v1/chat/completions"
    assert body["messages"][0] == {"role": "system", "content": "Be terse."}
    assert body["messages"][1]["content"] == "# Tray"


def test_extra_instructions_are_prepended(client: TestClient, stub):
    server = stub(StubLlm("ollama", models=["llama3.1"]))
    client.put(
        "/api/settings/llm",
        json={"provider": "ollama", "base_url": "192.168.1.50:11434", "model": "llama3.1"},
    )

    run_polish(client, text="# Tray", instructions="Make it shorter.")

    _, body = next(r for r in server.requests if r[0] == "/api/generate")
    assert body["prompt"].startswith("Make it shorter.")
    assert "# Tray" in body["prompt"]


def test_polish_without_a_model_says_so(client: TestClient, stub):
    stub(StubLlm("ollama", models=["llama3.1"]))
    client.put("/api/settings/llm", json={"provider": "ollama", "base_url": "192.168.1.50:11434"})

    result = run_polish(client, text="# Tray")
    assert result["status"] == "failed"
    assert "model" in result["error"].lower()


# --------------------------------------------------- progress and cancelling


def configure(client: TestClient, provider: str = "ollama") -> None:
    client.put(
        "/api/settings/llm",
        json={
            "provider": provider,
            "base_url": "192.168.1.50:11434" if provider == "ollama" else "192.168.1.42:1234",
            "model": "llama3.1",
        },
    )


class SlowGeneration:
    """Stands in for a model that takes its time, without blocking the loop.

    A synchronous sleep inside the transport would stall the event loop, which a
    real network call never does — and would make these tests prove nothing.
    """

    def __init__(self, delay: float, reply: str = "Tighter."):
        self.delay = delay
        self.reply = reply
        self.started = 0
        self.completed = 0
        self.cancelled = 0

    async def __call__(self, *args, **kwargs) -> str:
        import asyncio

        self.started += 1
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.completed += 1
        return self.reply


@pytest.fixture
def slow_polish(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Replace only the generation call, keeping the run/poll/cancel machinery real."""

    def install(delay: float, reply: str = "Tighter.") -> SlowGeneration:
        generation = SlowGeneration(delay, reply)
        state = client.app.state.app_state  # type: ignore[attr-defined]
        monkeypatch.setattr(state.llm, "polish", generation)
        return generation

    return install


def wait_until_settled(client: TestClient, run_id: str, attempts: int = 300) -> dict:
    import time

    for _ in range(attempts):
        state = client.get(f"/api/llm/polish/{run_id}").json()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    raise AssertionError("run never settled")


def test_polish_returns_a_run_id_immediately(client: TestClient, slow_polish):
    configure(client)
    slow_polish(delay=2.0)

    import time

    began = time.monotonic()
    started = client.post("/api/llm/polish", json={"text": "# Tray"})
    took = time.monotonic() - began

    assert started.status_code == 202
    body = started.json()
    assert body["status"] == "running"
    assert body["id"]
    # The whole point of the popup: this returns long before the model does.
    assert took < 0.5


def test_polling_reports_progress_then_the_result(client: TestClient, slow_polish):
    configure(client)
    slow_polish(delay=0.3, reply="Tighter.")

    run_id = client.post("/api/llm/polish", json={"text": "# Tray"}).json()["id"]

    progress = client.get(f"/api/llm/polish/{run_id}").json()
    assert progress["status"] == "running"
    assert progress["elapsed_seconds"] >= 0

    state = wait_until_settled(client, run_id)
    assert state["status"] == "done"
    assert state["polished"] == "Tighter."
    assert state["original"] == "# Tray"


def test_cancelling_stops_the_run(client: TestClient, slow_polish):
    configure(client)
    generation = slow_polish(delay=30.0)

    run_id = client.post("/api/llm/polish", json={"text": "# Tray"}).json()["id"]
    # Let the task actually reach the generation call before pulling the rug.
    import time

    for _ in range(100):
        if generation.started:
            break
        client.get(f"/api/llm/polish/{run_id}")
        time.sleep(0.02)

    cancelled = client.post(f"/api/llm/polish/{run_id}/cancel").json()
    assert cancelled["status"] == "cancelled"

    # Stays cancelled rather than completing behind the user's back...
    assert client.get(f"/api/llm/polish/{run_id}").json()["status"] == "cancelled"
    # ...and the generation was genuinely aborted, not merely ignored, which is
    # what frees the model on the other machine.
    for _ in range(100):
        if generation.cancelled:
            break
        time.sleep(0.02)
    assert generation.cancelled == 1
    assert generation.completed == 0


def test_cancelling_twice_is_harmless(client: TestClient, slow_polish):
    configure(client)
    slow_polish(delay=30.0)

    run_id = client.post("/api/llm/polish", json={"text": "# Tray"}).json()["id"]
    client.post(f"/api/llm/polish/{run_id}/cancel")

    assert client.post(f"/api/llm/polish/{run_id}/cancel").status_code == 200


def test_a_failing_run_reports_its_error(client: TestClient, monkeypatch):
    configure(client)

    async def boom(*args, **kwargs):
        raise RuntimeError("model not loaded")

    state = client.app.state.app_state  # type: ignore[attr-defined]
    monkeypatch.setattr(state.llm, "polish", boom)

    run_id = client.post("/api/llm/polish", json={"text": "# Tray"}).json()["id"]
    settled = wait_until_settled(client, run_id)

    assert settled["status"] == "failed"
    assert settled["error"] == "model not loaded"


def test_an_unknown_run_is_404(client: TestClient):
    assert client.get("/api/llm/polish/NOPE").status_code == 404
    assert client.post("/api/llm/polish/NOPE/cancel").status_code == 404


def test_empty_text_is_refused_before_starting_a_run(client: TestClient, stub):
    stub(StubLlm("ollama", models=["llama3.1"]))
    configure(client)

    assert client.post("/api/llm/polish", json={"text": "   "}).status_code == 422


def test_health_reports_the_configured_provider(client: TestClient):
    client.put(
        "/api/settings/llm",
        json={"provider": "lmstudio", "base_url": "192.168.1.42:1234", "model": "qwen"},
    )

    body = client.get("/api/health").json()
    assert body["llm_configured"] is True
    assert body["llm_provider"] == "lmstudio"
