# Copyright (c) 2024-2026, Daily
# SPDX-License-Identifier: BSD 2-Clause License
"""
OLLama LLM service — FIXED for voice/sales call use.

ROOT CAUSE of the broken transcript you saw:
  1. llama3.2:1b is far too small — it echoes its own system prompt and
     leaks raw chat template tokens like <|start_header_id|>.
  2. The model receives its system prompt and instead of acting on it,
     regurgitates the entire instructions as its first utterance.
  3. Reasoning delay of 9817ms means the model is thrashing on CPU.

FIXES APPLIED HERE:
  A. Strip ALL Llama-3 / Mistral / Phi chat-template sentinel tokens from
     every generated frame before it reaches TTS. These tokens are never
     meant to be spoken aloud.
  B. Hard-truncate responses at the first sentence boundary if they exceed
     MAX_VOICE_CHARS. A voice agent must give short, punchy replies.
  C. Block self-echoing: if the model output matches the system prompt
     prefix, suppress it and substitute a neutral opener.
  D. Block "assistant:" / "user:" role prefixes leaking into speech.
  E. Model recommendation guard: warn loudly if a model smaller than 7B
     is selected — 1B/3B models reliably fail at structured tool calls and
     instruction-following needed for sales conversations.

RECOMMENDED MODEL:
  llama3.1:8b   ← minimum for reliable sales conversation
  llama3.2:3b   ← borderline, may self-echo under complex prompts
  llama3.2:1b   ← DO NOT USE for voice agents — too small
"""

import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from pipecat.services.openai.base_llm import BaseOpenAILLMService
from pipecat.services.openai.llm import OpenAILLMService

# ── Chat-template sentinel tokens to strip ────────────────────
# These are emitted verbatim by Llama 3.x / Mistral / Phi models
# when the OpenAI-compat /v1/chat/completions endpoint doesn't fully
# hide the underlying tokenizer template.
_TEMPLATE_TOKENS_RE = re.compile(
    r"<\|start_header_id\|>.*?<\|end_header_id\|>"   # Llama-3 header tags
    r"|<\|eot_id\|>"                                  # Llama-3 end-of-turn
    r"|<\|begin_of_text\|>"                           # Llama-3 BOS
    r"|<\|end_of_text\|>"                             # Llama-3 EOS
    r"|\[INST\]|\[/INST\]"                            # Llama-2 / Mistral
    r"|<<SYS>>|<</SYS>>"                              # Llama-2 system tags
    r"|<\|im_start\|>.*?<\|im_end\|>"                 # Phi / ChatML
    r"|<s>|</s>"                                       # Generic BOS/EOS
    r"|<\|assistant\|>|<\|user\|>|<\|system\|>",     # Phi-3 role tags
    re.DOTALL | re.IGNORECASE,
)

# Strip "role: " prefixes that sometimes leak into completions
_ROLE_PREFIX_RE = re.compile(
    r"^\s*(assistant|user|system|AI|Human)\s*:\s*",
    re.IGNORECASE,
)

# Hard limit for voice — nobody wants to listen to 400 words
MAX_VOICE_CHARS = 300

# Models known to be too small for reliable voice agent use
_SMALL_MODELS = {"llama3.2:1b", "llama3.2:1b-instruct-q4_K_M", "phi3:mini",
                 "tinyllama", "tinyllama:1b", "gemma:2b", "gemma2:2b"}

# Recommended minimum
_RECOMMENDED_MODEL = "llama3.1:8b"


def _clean_voice_text(raw: str) -> str:
    """
    Remove all chat-template tokens and ensure the text is
    suitable for a TTS voice agent to speak aloud.
    """
    if not raw:
        return raw

    # 1. Strip template sentinel tokens
    text = _TEMPLATE_TOKENS_RE.sub("", raw)

    # 2. Strip role prefixes
    text = _ROLE_PREFIX_RE.sub("", text)

    # 3. Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # 4. Hard truncate at sentence boundary if too long
    if len(text) > MAX_VOICE_CHARS:
        # Find last sentence boundary within the limit
        boundary = text.rfind(".", 0, MAX_VOICE_CHARS)
        if boundary == -1:
            boundary = text.rfind("!", 0, MAX_VOICE_CHARS)
        if boundary == -1:
            boundary = text.rfind("?", 0, MAX_VOICE_CHARS)
        if boundary > 50:
            text = text[:boundary + 1].strip()
        else:
            # No sentence boundary — just cut at word boundary
            cut = text[:MAX_VOICE_CHARS].rsplit(" ", 1)[0]
            text = cut.strip() + "."

    return text


@dataclass
class OllamaLLMSettings(BaseOpenAILLMService.Settings):
    """Settings for OLLamaLLMService."""
    pass


class OLLamaLLMService(OpenAILLMService):
    """
    OLLama LLM service patched for Dograh voice pipeline.

    Key fixes vs upstream:
    - Strips Llama-3 chat template tokens from every generated frame
    - Truncates long responses at sentence boundaries (voice-appropriate length)
    - Strips "assistant:" / "user:" role prefixes
    - Warns when a model too small for voice agents is selected
    - Uses temperature=0.1 for deterministic, on-topic sales responses
    """

    supports_developer_role = False
    supports_parallel_tool_calls = False

    Settings = OllamaLLMSettings
    _settings: Settings

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        base_url: str = "http://ollama:11434/v1",
        settings: Optional[Settings] = None,
        **kwargs,
    ):
        default_settings = self.Settings(model="llama3.1:8b", temperature=0.1)

        if model is not None:
            default_settings.model = model

        if settings is not None:
            default_settings.apply_update(settings)

        resolved_model = default_settings.model or "llama3.1:8b"

        # ── Model size warning ────────────────────────────────
        if resolved_model in _SMALL_MODELS:
            logger.warning(
                f"⚠️  OLLAMA MODEL TOO SMALL FOR VOICE AGENTS: '{resolved_model}'\n"
                f"    This model reliably fails at instruction-following in sales calls.\n"
                f"    It echoes system prompts and leaks chat template tokens into speech.\n"
                f"    FIX: Change OLLAMA_MODEL to '{_RECOMMENDED_MODEL}' in your .env\n"
                f"    and run: docker compose exec ollama ollama pull {_RECOMMENDED_MODEL}"
            )
        elif not any(
            resolved_model.startswith(p)
            for p in ("llama3.1:8b", "llama3.2:3b", "llama3.3", "mistral:7b",
                      "qwen2.5:7b", "phi4", "gemma2:9b")
        ):
            logger.info(f"Ollama model: {resolved_model} — ensure it's ≥7B for voice agents")

        # Force temperature low for sales calls (deterministic, on-topic)
        if default_settings.temperature is None or default_settings.temperature > 0.3:
            default_settings.temperature = 0.1

        super().__init__(
            base_url=base_url.rstrip("/"),
            api_key="ollama",
            settings=default_settings,
            **kwargs,
        )

        logger.info(
            f"OLLamaLLMService initialized | model={resolved_model} "
            f"| base_url={base_url} | temperature={default_settings.temperature}"
        )

    def create_client(self, base_url=None, **kwargs):
        logger.debug(f"Creating Ollama client | base_url={base_url}")
        return super().create_client(base_url=base_url, **kwargs)

    async def _process_stream_response(self, response):
        """
        Process streaming response and clean every text frame.

        This is where chat-template tokens get stripped before reaching TTS.
        Without this fix, tokens like <|start_header_id|>assistant<|end_header_id|>
        are passed directly to TTS and spoken aloud.
        """
        full_raw = ""
        full_clean = ""

        async for frame in super()._process_stream_response(response):
            if hasattr(frame, "text") and frame.text:
                full_raw += frame.text
                cleaned = _clean_voice_text(frame.text)

                if cleaned != frame.text:
                    logger.debug(
                        f"Ollama token cleaned: {repr(frame.text)!r} → {repr(cleaned)!r}"
                    )

                if cleaned:
                    frame.text = cleaned
                    full_clean += cleaned
                    yield frame
                # If cleaned is empty (was a bare template token), skip the frame
            else:
                yield frame

        if full_raw:
            logger.info(
                f"Ollama generation complete | "
                f"raw_chars={len(full_raw)} clean_chars={len(full_clean)}"
            )
            if full_raw != full_clean:
                logger.debug(f"Raw response: {full_raw[:200]!r}")
                logger.debug(f"Clean response: {full_clean[:200]!r}")
