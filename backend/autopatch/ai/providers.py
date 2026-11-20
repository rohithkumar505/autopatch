"""AI providers behind one interface: Anthropic's Claude API, or a local model through Ollama.

To add another provider, implement AIProvider.generate (return a validated pydantic object)
and AIProvider.test_connection, then add it to get_provider().
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class AIError(RuntimeError):
    """A user-facing AI failure."""


class AINotConfigured(AIError):
    pass


class AIRefusal(AIError):
    pass


@dataclass
class AIUsage:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        if self.provider == "autopatch-core":
            return f"{self.model}, {self.duration_s:.1f}s, runs locally"
        cache = f", {self.cache_read_tokens:,} from cache" if self.cache_read_tokens else ""
        return (f"{self.model}: {self.input_tokens + self.cache_read_tokens + self.cache_write_tokens:,} input tokens"
                f"{cache}, {self.output_tokens:,} output tokens, {self.duration_s:.0f}s")


class AIProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def generate(self, *, system: str, context: str, task: str, schema: type[T], effort: str,
                 max_tokens: int = 64000) -> tuple[T, AIUsage]: ...

    @abstractmethod
    def test_connection(self) -> dict: ...


WORKSPACE_HELP = ("This API key is not tied to a workspace. Enter your workspace ID in Settings > AI provider "
                  "(find it in the Anthropic Console under Settings > Workspaces), or create a new key inside a workspace.")


def _needs_workspace(exc: Exception) -> bool:
    return "anthropic-workspace-id" in str(exc)


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None, model: str, fallbacks: bool = True, workspace_id: str = ""):
        import anthropic

        self._anthropic = anthropic
        # Keys that are not tied to a workspace must name one on every request.
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        # With no key here, the SDK resolves ANTHROPIC_API_KEY / an `ant auth login` profile itself.
        self.client = anthropic.Anthropic(api_key=api_key or None, max_retries=3, default_headers=headers)
        self.model = model
        self.fallbacks = fallbacks

    def generate(self, *, system, context, task, schema, effort, max_tokens=64000):
        a = self._anthropic
        extra = {}
        if self.fallbacks and self.model.startswith(("claude-opus-5", "claude-fable-5", "claude-sonnet-5-5")):
            # On a safety decline, the API retries on Anthropic's recommended fallback model.
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        start = time.monotonic()
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system}],
                messages=[{
                    "role": "user",
                    "content": [
                        # The project listing is identical across calls for the same version,
                        # so it is cached and later calls only pay for the task text.
                        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": task},
                    ],
                }],
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                output_format=schema,
                **extra,
            ) as stream:
                message = stream.get_final_message()
        except a.AuthenticationError as exc:
            raise AIError("The Anthropic API key was rejected. Check it in Settings > AI provider.") from exc
        except a.PermissionDeniedError as exc:
            raise AIError(f"The API key cannot use {self.model}: {exc.message}") from exc
        except a.NotFoundError as exc:
            raise AIError(f"Model {self.model} was not found. Check the model name in Settings.") from exc
        except a.RateLimitError as exc:
            raise AIError("Anthropic rate limit reached; wait a minute and try again.") from exc
        except a.BadRequestError as exc:
            if _needs_workspace(exc):
                raise AIError(WORKSPACE_HELP) from exc
            raise AIError(f"The AI request was rejected: {exc.message}") from exc
        except a.APIStatusError as exc:
            raise AIError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise AIError("Could not reach the Anthropic API. Check your internet connection.") from exc

        usage = AIUsage(
            provider=self.name, model=message.model, duration_s=round(time.monotonic() - start, 1),
            input_tokens=message.usage.input_tokens or 0, output_tokens=message.usage.output_tokens or 0,
            cache_read_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
        )
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise AIRefusal(f"The model declined this request (category: {category or 'unspecified'}).")
        if message.stop_reason == "max_tokens":
            raise AIError("The AI response was cut off at the token limit. Try a smaller project or lower effort.")
        parsed = message.parsed_output
        if parsed is None:
            raise AIError("The AI response did not match the expected format.")
        return parsed, usage

    def test_connection(self) -> dict:
        a = self._anthropic
        try:
            info = self.client.models.retrieve(self.model)
        except a.AuthenticationError:
            return {"ok": False, "message": "API key rejected"}
        except a.NotFoundError:
            return {"ok": False, "message": f"Model {self.model} not found for this key"}
        except a.APIConnectionError:
            return {"ok": False, "message": "Could not reach api.anthropic.com"}
        except a.APIStatusError as exc:
            if _needs_workspace(exc):
                return {"ok": False, "message": WORKSPACE_HELP}
            return {"ok": False, "message": f"API error {exc.status_code}: {exc.message}"}
        except Exception as exc:  # missing credentials raise a plain error from the SDK
            return {"ok": False, "message": str(exc)}
        window = getattr(info, "max_input_tokens", None)
        return {"ok": True, "message": f"Connected: {getattr(info, 'display_name', self.model)}"
                + (f", {window:,}-token context" if window else "")}


class OllamaProvider(AIProvider):
    """A model running locally with Ollama (https://ollama.com). Nothing leaves the machine."""

    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model

    def _post(self, path: str, body: dict, timeout: float) -> dict:
        req = urllib.request.Request(self.base_url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-configured local URL
            return json.loads(resp.read())

    def generate(self, *, system, context, task, schema, effort, max_tokens=64000):
        start = time.monotonic()
        body = {
            "model": self.model,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": 32768},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": context + "\n\n" + task},
            ],
        }
        try:
            data = self._post("/api/chat", body, timeout=1800)
        except urllib.error.URLError as exc:
            raise AIError(f"Could not reach Ollama at {self.base_url}: {exc.reason}") from exc
        content = (data.get("message") or {}).get("content", "")
        try:
            parsed = schema.model_validate_json(content)
        except ValidationError as exc:
            raise AIError(f"The local model's answer did not match the expected format: {exc.errors()[:1]}") from exc
        usage = AIUsage(provider=self.name, model=self.model, duration_s=round(time.monotonic() - start, 1),
                        input_tokens=data.get("prompt_eval_count", 0), output_tokens=data.get("eval_count", 0))
        return parsed, usage

    def test_connection(self) -> dict:
        try:
            with urllib.request.urlopen(self.base_url + "/api/tags", timeout=5) as resp:  # noqa: S310
                models = [m.get("name", "") for m in json.loads(resp.read()).get("models", [])]
        except (urllib.error.URLError, OSError) as exc:
            return {"ok": False, "message": f"Ollama is not reachable at {self.base_url} ({exc})"}
        if not any(m == self.model or m.split(":")[0] == self.model for m in models):
            return {"ok": False, "message": f"Ollama is running but model {self.model} is not pulled. "
                                            f"Available: {', '.join(models) or 'none'}"}
        return {"ok": True, "message": f"Connected to Ollama; {self.model} is available"}


def get_provider(settings: dict) -> AIProvider:
    kind = settings["ai.provider"]
    if kind == "anthropic":
        key = settings["ai.anthropic.api_key"] or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise AINotConfigured("No Anthropic API key is set. Add one in Settings > AI provider.")
        return AnthropicProvider(key or None, settings["ai.anthropic.model"], settings["ai.anthropic.fallbacks"],
                                 settings.get("ai.anthropic.workspace_id", ""))
    if kind == "ollama":
        return OllamaProvider(settings["ai.ollama.url"], settings["ai.ollama.model"])
    raise AINotConfigured("AI is turned off in Settings. Choose a provider to enable AI analysis and fixes.")


def ai_status(settings: dict) -> dict:
    if settings["ai.provider"] == "builtin":
        return {"configured": True, "provider": "builtin", "model": "AutoPatch Core"}
    try:
        provider = get_provider(settings)
    except AINotConfigured as exc:
        return {"configured": False, "provider": settings["ai.provider"], "message": str(exc)}
    return {"configured": True, "provider": provider.name, "model": provider.model}
