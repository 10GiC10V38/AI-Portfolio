"""
shared/llm/client.py

LLM client abstraction layer.
Polling agents  → Gemini 2.0 Flash (free tier, 1500 RPD).
Advisor / chat  → Claude Haiku (on-demand, very cheap) with Gemini fallback.
Phase 2         → GPT-4o + consensus scoring.

NEVER import anthropic or google.generativeai directly in agent code.
Always use get_provider() from this module.
"""

from __future__ import annotations
import os
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Response dataclass ────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    raw: dict                          # full API response for audit log


# ── Base provider interface ───────────────────────────────────────────────────

class LLMProvider(ABC):

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        ...

    @abstractmethod
    def complete_chat(
        self,
        system_prompt: str,
        messages: list[dict],          # [{"role": "user"|"assistant", "content": "..."}]
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Multi-turn chat — used by the advisor agent."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...


# ── Claude provider ───────────────────────────────────────────────────────────

class ClaudeProvider(LLMProvider):
    """
    Haiku for cheap on-demand calls (advisor chat).
    Sonnet for deep analysis when quality matters.
    """

    HAIKU_MODEL  = "claude-haiku-4-5-20251001"
    SONNET_MODEL = "claude-sonnet-4-6"

    def __init__(self, api_key: str, use_sonnet: bool = False):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model  = self.SONNET_MODEL if use_sonnet else self.HAIKU_MODEL

    @property
    def provider_name(self) -> str:
        return "claude"

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        logger.debug(f"[claude] {self._model} | ~{len(user_prompt)//4} tokens")
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as e:
            logger.error(f"[claude] complete() API call failed: {e}")
            raise
        return LLMResponse(
            content=message.content[0].text,
            provider="claude",
            model=self._model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            raw=message.model_dump(),
        )

    def complete_chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        logger.debug(f"[claude] chat {self._model} | {len(messages)} turns")
        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
            )
        except Exception as e:
            logger.error(f"[claude] complete_chat() API call failed: {e}")
            raise
        return LLMResponse(
            content=message.content[0].text,
            provider="claude",
            model=self._model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            raw=message.model_dump(),
        )


# ── Gemini provider (free tier) ───────────────────────────────────────────────

class GeminiProvider(LLMProvider):
    """
    gemini-2.0-flash-lite  → polling agents (30 RPM, 1500 RPD free)
    gemini-2.0-flash       → chat / deep analysis (15 RPM, 1500 RPD free)
    """

    LITE_MODEL = "gemini-flash-latest"
    FULL_MODEL = "gemini-flash-latest"

    def __init__(self, api_key: str, use_full: bool = False):
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._genai = genai
        self._model = self.FULL_MODEL if use_full else self.LITE_MODEL

    @property
    def provider_name(self) -> str:
        return "gemini"

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        logger.debug(f"[gemini] {self._model} | ~{len(user_prompt)//4} tokens")

        try:
            model = self._genai.GenerativeModel(
                model_name=self._model,
                system_instruction=system_prompt,
                generation_config=self._genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )
            resp = model.generate_content(user_prompt)
            text = resp.text
        except Exception as e:
            logger.error(f"[gemini] complete() API call failed: {e}")
            raise

        in_tok  = getattr(resp.usage_metadata, "prompt_token_count",      0) or 0
        out_tok = getattr(resp.usage_metadata, "candidates_token_count",  0) or 0

        return LLMResponse(
            content=text,
            provider="gemini",
            model=self._model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            raw={"text": text, "model": self._model},
        )

    def complete_chat(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        logger.debug(f"[gemini] chat {self._model} | {len(messages)} turns")

        try:
            model = self._genai.GenerativeModel(
                model_name=self._model,
                system_instruction=system_prompt,
                generation_config=self._genai.types.GenerationConfig(
                    max_output_tokens=max_tokens,
                    temperature=0.3,
                ),
            )

            # Convert to Gemini history format (all but last message)
            history = [
                {
                    "role":  "user" if m["role"] == "user" else "model",
                    "parts": [m["content"]],
                }
                for m in messages[:-1]
            ]
            chat = model.start_chat(history=history)
            resp = chat.send_message(messages[-1]["content"])
            text = resp.text
        except Exception as e:
            logger.error(f"[gemini] complete_chat() API call failed: {e}")
            raise

        in_tok  = getattr(resp.usage_metadata, "prompt_token_count",      0) or 0
        out_tok = getattr(resp.usage_metadata, "candidates_token_count",  0) or 0

        return LLMResponse(
            content=text,
            provider="gemini",
            model=self._model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            raw={"text": text, "model": self._model},
        )


# ── GPT provider (Phase 2 stub) ───────────────────────────────────────────────
# To implement: pip install openai, then fill in with openai.OpenAI client.
# Model tiers: use_sonnet=False → gpt-4o-mini, use_sonnet=True → gpt-4o

class GPTProvider(LLMProvider):
    @property
    def provider_name(self) -> str:
        return "gpt"

    def complete(self, system_prompt, user_prompt, max_tokens=1024, temperature=0.2):
        raise NotImplementedError("GPTProvider — Phase 2")

    def complete_chat(self, system_prompt, messages, max_tokens=1024):
        raise NotImplementedError("GPTProvider — Phase 2")


# ── Adding a new provider ─────────────────────────────────────────────────────
# 1. Subclass LLMProvider and implement complete() + complete_chat()
# 2. Register it in _PROVIDER_REGISTRY below
# 3. Set LLM_PROVIDER=yourname in .env — nothing else to change
#
# Example skeleton:
#
#   class MistralProvider(LLMProvider):
#       @property
#       def provider_name(self): return "mistral"
#       def complete(self, system_prompt, user_prompt, max_tokens=1024, temperature=0.2):
#           ...
#       def complete_chat(self, system_prompt, messages, max_tokens=1024):
#           ...


# ── Provider registry ─────────────────────────────────────────────────────────
# Maps LLM_PROVIDER env value → (builder_fn(secrets, use_sonnet) → LLMProvider)
# Add new providers here without touching get_provider().

def _build_gemini(secrets: dict, use_sonnet: bool) -> LLMProvider:
    return GeminiProvider(api_key=secrets["GEMINI_API_KEY"], use_full=use_sonnet)

def _build_claude(secrets: dict, use_sonnet: bool) -> LLMProvider:
    key = secrets.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.warning("ANTHROPIC_API_KEY not set — falling back to Gemini")
        return _build_gemini(secrets, use_sonnet)
    return ClaudeProvider(api_key=key, use_sonnet=use_sonnet)

def _build_gpt(secrets: dict, use_sonnet: bool) -> LLMProvider:
    return GPTProvider()

_PROVIDER_REGISTRY: dict[str, object] = {
    "gemini": _build_gemini,
    "claude": _build_claude,
    "gpt":    _build_gpt,
    # "mistral": _build_mistral,   ← add new providers here
}


# ── Consensus orchestrator (Phase 2 stub) ────────────────────────────────────

@dataclass
class ConsensusResult:
    agreement_pct: int
    majority_verdict: str
    individual_responses: list[LLMResponse]
    conflict_summary: Optional[str]


def consensus(
    providers: list[LLMProvider],
    system_prompt: str,
    user_prompt: str,
) -> ConsensusResult:
    if len(providers) == 1:
        response = providers[0].complete(system_prompt, user_prompt)
        return ConsensusResult(
            agreement_pct=100,
            majority_verdict=response.content,
            individual_responses=[response],
            conflict_summary=None,
        )
    raise NotImplementedError("Multi-provider consensus — Phase 2")


# ── Factory ───────────────────────────────────────────────────────────────────

def get_provider(
    provider_name: str = "gemini",
    use_sonnet: bool = False,      # maps to "use full/pro model" for any provider
) -> LLMProvider:
    """
    Factory — looks up _PROVIDER_REGISTRY by name.
    Set LLM_PROVIDER=<name> in env to switch providers.
    use_sonnet=True selects the higher-quality model tier for that provider.
    """
    builder = _PROVIDER_REGISTRY.get(provider_name)
    if builder is None:
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. "
            f"Available: {list(_PROVIDER_REGISTRY.keys())}"
        )
    secrets = _load_secrets()
    return builder(secrets, use_sonnet)  # type: ignore[operator]


# ── Secret loader ─────────────────────────────────────────────────────────────

def _load_secrets() -> dict:
    source = os.getenv("SECRETS_SOURCE", "env")

    if source == "env":
        return {
            "GEMINI_API_KEY":    _require_env("GEMINI_API_KEY"),
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),  # optional
            "NEWS_API_KEY":      os.getenv("NEWS_API_KEY", ""),
            "YOUTUBE_API_KEY":   os.getenv("YOUTUBE_API_KEY", ""),
            "ALPHA_VANTAGE_KEY": os.getenv("ALPHA_VANTAGE_KEY", ""),
        }

    if source == "gcp":
        return _load_from_gcp_secret_manager()

    raise ValueError(f"Unknown SECRETS_SOURCE: {source!r}")


def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Required env var '{key}' is not set.")
    return val


def _load_from_gcp_secret_manager() -> dict:
    try:
        from google.cloud import secretmanager
        client  = secretmanager.SecretManagerServiceClient()
        project = os.environ["GCP_PROJECT_ID"]

        def access(name: str) -> str:
            path = f"projects/{project}/secrets/{name}/versions/latest"
            resp = client.access_secret_version(request={"name": path})
            return resp.payload.data.decode("UTF-8")

        return {
            "GEMINI_API_KEY":    access("gemini-api-key"),
            "ANTHROPIC_API_KEY": access("anthropic-api-key"),
            "NEWS_API_KEY":      access("news-api-key"),
            "YOUTUBE_API_KEY":   access("youtube-api-key"),
            "ALPHA_VANTAGE_KEY": access("alpha-vantage-key"),
        }
    except Exception as e:
        logger.error(f"GCP Secret Manager load failed: {e}")
        raise
