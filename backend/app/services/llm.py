"""Optional LLM polish against a local server on another machine.

Two providers, both spoken to over the LAN by IP:

* **Ollama** — its native API (`/api/tags`, `/api/generate`).
* **LM Studio** — the OpenAI-compatible API (`/v1/models`, `/v1/chat/completions`),
  which also covers llama.cpp's server, vLLM and friends.

Settings live in `_shared/llm.yaml` inside the library, so they are editable in
the UI, readable in a text editor, and copied along with a folder backup. The
`FORGE_OLLAMA_*` environment variables seed the file on first run.

Every request is made **by the backend**, never the browser. On a NAS that is
the whole point: the container reaches the LLM host, and the browser never has
to. It also means `localhost` in the URL refers to the container itself, which
is the single most common way to misconfigure this — see `_connection_hint`.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml

from ..config import Settings

log = logging.getLogger(__name__)

SETTINGS_FILE = "_shared/llm.yaml"

PROVIDERS = ("ollama", "lmstudio")

DEFAULT_PORTS = {"ollama": 11434, "lmstudio": 1234}

DEFAULT_SYSTEM_PROMPT = """You tighten 3D-printing model descriptions for MakerWorld.

Rules:
- Keep every factual detail: dimensions, print settings, filament, licence, credits.
- Keep the existing Markdown structure and heading levels.
- Cut padding and marketing language. Plain, direct, friendly.
- Never invent facts, links, or settings that are not in the input.
- Return only the rewritten Markdown, with no preamble or commentary."""


@dataclass
class LlmSettings:
    provider: str = "ollama"
    base_url: str = ""
    model: str = ""
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    temperature: float = 0.3
    timeout_seconds: float = 120.0

    @property
    def configured(self) -> bool:
        return bool(self.base_url.strip())

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LlmSettings:
        provider = str(raw.get("provider") or "ollama").lower()
        return cls(
            provider=provider if provider in PROVIDERS else "ollama",
            base_url=str(raw.get("base_url") or "").strip(),
            model=str(raw.get("model") or "").strip(),
            system_prompt=str(
                raw.get("system_prompt")
                if raw.get("system_prompt") is not None
                else DEFAULT_SYSTEM_PROMPT
            ),
            temperature=_clamp(raw.get("temperature"), 0.0, 2.0, 0.3),
            timeout_seconds=_clamp(raw.get("timeout_seconds"), 5.0, 900.0, 120.0),
        )


def _clamp(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return fallback


def normalise_base_url(url: str, provider: str) -> str:
    """Accept `192.168.1.50`, `192.168.1.50:1234` or a full URL."""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = f"http://{url}"
    parsed = urlparse(url)
    if not parsed.hostname:
        return ""
    # LM Studio is usually pasted with the /v1 suffix already on it.
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    port = parsed.port or DEFAULT_PORTS.get(provider, 11434)
    return f"{parsed.scheme}://{parsed.hostname}:{port}{path}"


def _is_loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in {"localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _connection_hint(settings: LlmSettings, error: str) -> str:
    """Turn a bare transport failure into something actionable."""
    if _is_loopback(settings.base_url):
        return (
            "This points at localhost, which from inside the container means the "
            "container itself, not your LLM machine. Use the LAN IP of the host "
            "running it, e.g. http://192.168.1.50:"
            f"{DEFAULT_PORTS.get(settings.provider, 11434)}."
        )
    if settings.provider == "ollama":
        return (
            "Ollama only listens on 127.0.0.1 by default, so it refuses connections "
            "from other machines. Start it with OLLAMA_HOST=0.0.0.0 and check the "
            "firewall on that host."
        )
    return (
        "LM Studio only serves other machines when 'Serve on Local Network' is "
        "switched on in its Developer/Server tab. Check that and the host firewall."
    )


class LlmService:
    """Reads and writes `_shared/llm.yaml`, and talks to whichever server it names."""

    def __init__(self, settings: Settings, library_root: Path):
        self.env = settings
        self.library_root = library_root
        self._cache: LlmSettings | None = None

    # ------------------------------------------------------------- settings

    @property
    def settings_path(self) -> Path:
        return self.library_root / SETTINGS_FILE

    def load(self) -> LlmSettings:
        if self._cache is not None:
            return self._cache

        raw: dict[str, Any] = {}
        if self.settings_path.exists():
            try:
                loaded = yaml.safe_load(self.settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except yaml.YAMLError as exc:
                log.warning("unreadable %s: %s", self.settings_path, exc)

        current = LlmSettings.from_dict(raw)
        if not raw and self.env.ollama_url.strip():
            # First run with FORGE_OLLAMA_URL set: keep working without anyone
            # having to open Settings. With no URL the model stays empty too,
            # rather than naming a model nobody has confirmed exists.
            current = replace(
                current,
                base_url=normalise_base_url(self.env.ollama_url, "ollama"),
                model=self.env.ollama_model,
                timeout_seconds=self.env.ollama_timeout_seconds,
            )
        self._cache = current
        return current

    def save(self, changes: dict[str, Any]) -> LlmSettings:
        current = self.load()
        merged = {**current.as_dict(), **{k: v for k, v in changes.items() if v is not None}}

        provider = str(merged.get("provider") or "ollama").lower()
        if provider not in PROVIDERS:
            raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
        merged["provider"] = provider
        merged["base_url"] = normalise_base_url(str(merged.get("base_url") or ""), provider)

        # An empty prompt would silently un-steer the model; fall back to the default.
        if not str(merged.get("system_prompt") or "").strip():
            merged["system_prompt"] = DEFAULT_SYSTEM_PROMPT

        updated = LlmSettings.from_dict(merged)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            yaml.safe_dump(updated.as_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        self._cache = updated
        return updated

    def invalidate(self) -> None:
        """Drop the cache, so a hand-edited llm.yaml is picked up."""
        self._cache = None

    @property
    def configured(self) -> bool:
        return self.load().configured

    # -------------------------------------------------------------- probing

    async def status(self, candidate: LlmSettings | None = None) -> dict[str, Any]:
        """Reach the server and list its models. Never raises."""
        current = candidate or self.load()
        if not current.configured:
            return {
                "available": False,
                "reason": "No server configured yet. Add one in Settings.",
                "provider": current.provider,
                "models": [],
            }

        try:
            models = await self._list_models(current)
        except httpx.HTTPStatusError as exc:
            return {
                "available": False,
                "reason": f"{current.base_url} answered {exc.response.status_code}",
                "hint": _connection_hint(current, str(exc)),
                "provider": current.provider,
                "base_url": current.base_url,
                "models": [],
            }
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            return {
                "available": False,
                "reason": f"Could not reach {current.base_url}: {exc}",
                "hint": _connection_hint(current, str(exc)),
                "provider": current.provider,
                "base_url": current.base_url,
                "models": [],
            }

        return {
            "available": True,
            "provider": current.provider,
            "base_url": current.base_url,
            "model": current.model,
            "models": models,
            "model_present": current.model in models if current.model else False,
        }

    async def _list_models(self, current: LlmSettings) -> list[str]:
        async with httpx.AsyncClient(timeout=8.0) as client:
            if current.provider == "ollama":
                response = await client.get(f"{current.base_url}/api/tags")
                response.raise_for_status()
                return sorted(m["name"] for m in response.json().get("models", []))

            response = await client.get(f"{current.base_url}/v1/models")
            response.raise_for_status()
            return sorted(m["id"] for m in response.json().get("data", []))

    # -------------------------------------------------------------- polish

    async def polish(self, text: str, *, instructions: str = "", model: str | None = None) -> str:
        current = self.load()
        if not current.configured:
            raise RuntimeError("No LLM server is configured")
        if not text.strip():
            raise ValueError("nothing to polish")

        chosen = (model or current.model or "").strip()
        if not chosen:
            raise RuntimeError("No model selected. Pick one in Settings.")

        prompt = text if not instructions else f"{instructions.strip()}\n\n---\n\n{text}"
        timeout = httpx.Timeout(current.timeout_seconds, connect=10.0)

        async with httpx.AsyncClient(timeout=timeout) as client:
            if current.provider == "ollama":
                response = await client.post(
                    f"{current.base_url}/api/generate",
                    json={
                        "model": chosen,
                        "system": current.system_prompt,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": current.temperature},
                    },
                )
                response.raise_for_status()
                polished = (response.json().get("response") or "").strip()
            else:
                response = await client.post(
                    f"{current.base_url}/v1/chat/completions",
                    json={
                        "model": chosen,
                        "messages": [
                            {"role": "system", "content": current.system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": current.temperature,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                choices = response.json().get("choices") or []
                polished = (
                    (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
                )

        if not polished:
            raise RuntimeError(f"{current.provider} returned an empty response")
        return polished
