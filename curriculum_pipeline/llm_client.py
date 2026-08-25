"""OpenAI-compatible LLM client.

Supports any endpoint that follows the OpenAI Chat Completions API shape,
including local servers (vLLM, llama.cpp, ollama with the OpenAI shim) and
commercial providers (OpenAI, Together, Groq, etc.).

Configuration via environment variables:
    LLM_API_KEY    API key (optional for local endpoints)
    LLM_BASE_URL   Base URL (default: http://localhost:20128/v1)
    LLM_TEXT_MODEL Default text model (default: MiniMax-M3-NanoGPT)
    LLM_VISION_MODEL Default vision model (default: MiniMax-M3-NanoGPT)
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Literal

from openai import OpenAI


def _client() -> OpenAI:
    api_key = os.environ.get("LLM_API_KEY") or "no-key"
    base_url = os.environ.get("LLM_BASE_URL") or "http://localhost:20128/v1"
    if api_key == "no-key" and "localhost" not in base_url and "127.0.0.1" not in base_url:
        raise RuntimeError(
            "LLM_API_KEY not set and LLM_BASE_URL doesn't look like a local "
            "endpoint. Set LLM_API_KEY for cloud providers, or set LLM_BASE_URL "
            "to a local endpoint (default: http://localhost:20128/v1)."
        )
    return OpenAI(api_key=api_key, base_url=base_url)


def default_text_model() -> str:
    return os.environ.get("LLM_TEXT_MODEL") or "MiniMax-M3-NanoGPT"


def default_vision_model() -> str:
    return os.environ.get("LLM_VISION_MODEL") or "MiniMax-M3-NanoGPT"


class LLMClient:
    """Thin wrapper around the OpenAI SDK for chat, JSON, and vision calls."""

    def __init__(self, client: OpenAI | None = None):
        self.client = client or _client()

    def chat(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """Plain chat completion, returns the assistant text."""
        kwargs: dict = {
            "model": model or default_text_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        resp = self.client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Chat completion with JSON mode where the model supports it.

        Falls back to regular chat if response_format is unsupported by the
        configured model. Caller is still responsible for parsing the JSON.
        """
        kwargs: dict = {
            "model": model or default_text_model(),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        try:
            resp = self.client.chat.completions.create(
                response_format={"type": "json_object"},
                **kwargs,
            )
        except Exception:
            resp = self.client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def vision(
        self,
        system: str,
        user: str,
        image_path: str | Path,
        *,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> str:
        """Chat completion with one attached image (vision-capable models only)."""
        image_path = Path(image_path)
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        suffix = image_path.suffix.lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix, "image/png")

        resp = self.client.chat.completions.create(
            model=model or default_vision_model(),
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{data}"},
                        },
                    ],
                },
            ],
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()


def extract_json(text: str) -> dict:
    """Parse JSON from a model response, tolerating ```json fences and prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def role_for_image(path: str | Path) -> Literal["image/jpeg", "image/png", "image/webp"]:
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"
