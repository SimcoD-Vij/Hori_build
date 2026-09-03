"""
Thin LLM wrapper. If ANTHROPIC_API_KEY is set, agents get real LLM
reasoning. If not, every agent falls back to deterministic template logic
so `docker compose up` works immediately with zero configuration --
this is the guarantee the project asked for: everything needed runs
out of the box, and gets smarter once you add a key.

To use a local model instead (Ollama, per the build spec), set
LLM_PROVIDER=ollama and OLLAMA_MODEL, and point OLLAMA_HOST.
"""
import os
import json
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "template")  # template | anthropic | ollama
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")


def llm_available() -> bool:
    return LLM_PROVIDER in ("anthropic", "ollama")


def call_llm(system_prompt: str, user_prompt: str, max_tokens=800) -> str:
    """Returns raw text. Callers handle parsing / fallback."""
    if LLM_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": max_tokens,
                  "system": system_prompt,
                  "messages": [{"role": "user", "content": user_prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    if LLM_PROVIDER == "ollama":
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": f"{system_prompt}\n\n{user_prompt}", "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "")

    raise RuntimeError("No LLM provider configured -- caller should use the template fallback path.")
