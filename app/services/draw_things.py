"""Draw Things local image generation.

Draw Things (https://drawthings.ai, GPL-v3,
https://github.com/drawthingsai/draw-things-community) runs Stable
Diffusion / SDXL / Flux entirely on-device via Apple's MLX/Core ML stack.
No account, no API key, no per-image cost — the only requirement is the
Draw Things macOS app running locally with its API Server enabled
(Settings -> Advanced -> API Server, protocol HTTP, default port 7860).

This talks to its Automatic1111-compatible ``/sdapi/v1/txt2img`` endpoint,
which is NOT OpenAI-compatible (different request/response shape, images
come back as a list of base64 strings rather than ``data[].b64_json``), so
it needs its own thin client rather than reusing the openai_image gateway.
"""

import base64
import os
from typing import Any, Mapping

import requests
from loguru import logger

from app.config import config

DEFAULT_BASE_URL = "http://127.0.0.1:7860"
DEFAULT_STEPS = 20
DEFAULT_TIMEOUT_SECONDS = 300  # local SDXL generation on modest hardware can take 60-90s


class DrawThingsError(RuntimeError):
    """Draw Things 请求或响应错误：本地免费生成，没有远端计费风险，失败可安全重试/跳过。"""


def _base_url() -> str:
    return str(
        config.app.get("draw_things_base_url", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    ).rstrip("/")


def is_enabled(settings: Mapping[str, Any] | None = None) -> bool:
    """本地免费网关不需要 key；默认视为已启用，真正的可用性在请求时探测。"""
    settings = config.app if settings is None else settings
    return not str(settings.get("draw_things_disabled", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _model() -> str:
    return str(config.app.get("draw_things_model", "") or "").strip()


def _negative_prompt() -> str:
    return str(config.app.get("draw_things_negative_prompt", "") or "").strip()


def _steps() -> int:
    try:
        value = int(config.app.get("draw_things_steps", DEFAULT_STEPS))
    except (TypeError, ValueError):
        return DEFAULT_STEPS
    return value if value > 0 else DEFAULT_STEPS


def generate_image(
    prompt: str,
    width: int,
    height: int,
) -> bytes:
    """提交一次本地 Draw Things 文生图，返回 PNG/解码后的原始图片字节。"""
    term = str(prompt or "").strip()
    if not term:
        raise DrawThingsError("Draw Things prompt must not be empty")

    payload: dict[str, Any] = {
        "prompt": term,
        "width": int(width),
        "height": int(height),
        "steps": _steps(),
    }
    negative_prompt = _negative_prompt()
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    model = _model()
    if model:
        payload["override_settings"] = {"sd_model_checkpoint": model}

    url = f"{_base_url()}/sdapi/v1/txt2img"
    logger.info(f"generating image with Draw Things (local): term={term!r}, size={width}x{height}")
    try:
        response = requests.post(url, json=payload, timeout=DEFAULT_TIMEOUT_SECONDS)
    except requests.exceptions.ConnectionError as exc:
        raise DrawThingsError(
            "Could not reach Draw Things at "
            f"{url}. Open the Draw Things app, enable Settings -> Advanced -> "
            f"API Server (HTTP, port 7860, localhost), and keep the app running."
        ) from exc
    except Exception as exc:
        raise DrawThingsError(
            f"Draw Things request failed: {type(exc).__name__}: {exc}"
        ) from exc

    if response.status_code != 200:
        raise DrawThingsError(
            f"Draw Things rejected the request: HTTP {response.status_code}, "
            f"{response.text[:300]}"
        )
    try:
        body = response.json()
    except Exception as exc:
        raise DrawThingsError(
            f"Draw Things returned an unreadable response: {type(exc).__name__}"
        ) from exc

    images = body.get("images") if isinstance(body, dict) else None
    if not isinstance(images, list) or not images:
        raise DrawThingsError("Draw Things response did not contain any images")
    try:
        return base64.b64decode(images[0])
    except Exception as exc:
        raise DrawThingsError(f"Draw Things returned an invalid base64 image: {exc}") from exc
