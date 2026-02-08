"""OpenAI-compatible text-to-image client."""

import base64
import logging
import re
from typing import Any

import openai

from src.config import get_config

logger = logging.getLogger("meowko.providers.image_gen")


class ImageGenClient:
    """Generates images from text prompts via an OpenAI-compatible API.

    Supports two modes controlled by ``tti.api``:

    * ``"images"`` (default) – calls ``/v1/images/generations``.
    * ``"chat"`` – calls ``/v1/chat/completions`` and extracts images
      from the response content blocks.
    """

    def __init__(self) -> None:
        config = get_config()
        tti = config.tti
        model_ref = tti.get("model", "")

        if not model_ref:
            raise ValueError("tti.model is not configured")

        resolved = config.resolve_provider_model(model_ref)

        self.client = openai.AsyncOpenAI(
            base_url=resolved["base_url"],
            api_key=resolved["api_key"],
            timeout=tti["timeout"],
        )
        self.model = resolved["model"]
        self.size = tti["size"]
        self.quality = tti["quality"]
        self.api = tti.get("api", "images")

    async def generate(self, prompt: str) -> bytes:
        """Generate an image from a text prompt.

        Returns:
            Image bytes (PNG).
        """
        if self.api == "chat":
            return await self._generate_via_chat(prompt)
        return await self._generate_via_images(prompt)

    async def _generate_via_images(self, prompt: str) -> bytes:
        """Generate using the /v1/images/generations endpoint."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "response_format": "b64_json",
        }
        if self.size:
            kwargs["size"] = self.size
        if self.quality:
            kwargs["quality"] = self.quality
        response = await self.client.images.generate(**kwargs)

        b64_data = response.data[0].b64_json
        image_bytes = base64.b64decode(b64_data)

        logger.info(
            "TTI [images] generated %d bytes for: %s",
            len(image_bytes), prompt[:80],
        )
        return image_bytes

    async def _generate_via_chat(self, prompt: str) -> bytes:
        """Generate using the /v1/chat/completions endpoint.

        Sends the prompt as a user message and extracts base64 image
        data from the response content blocks.
        """
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )

        message = response.choices[0].message
        raw: dict[str, Any] = message.model_dump()
        content = raw.get("content")

        image_bytes = self._extract_image(content)
        if not image_bytes:
            raise RuntimeError(
                "TTI [chat] response contained no image data"
            )

        logger.info(
            "TTI [chat] generated %d bytes for: %s",
            len(image_bytes), prompt[:80],
        )
        return image_bytes

    @staticmethod
    def _extract_image(content: Any) -> bytes | None:
        """Try to extract the first base64 image from response content.

        Handles two common formats:
        - List of content blocks with ``{"type": "image_url", ...}``
        - Plain string containing a ``data:image/...;base64,...`` URL
        """
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                # {"type": "image_url", "image_url": {"url": "data:...;base64,..."}}
                if block.get("type") == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    b = _decode_data_url(url)
                    if b:
                        return b
                # {"type": "image", "image_url": {"url": "data:..."}}  (variant)
                if block.get("type") == "image":
                    url = block.get("image_url", {}).get("url", "")
                    b = _decode_data_url(url)
                    if b:
                        return b

        if isinstance(content, str):
            b = _decode_data_url(content)
            if b:
                return b

        return None


def _decode_data_url(text: str) -> bytes | None:
    """Decode the first ``data:image/...;base64,...`` in *text*."""
    marker = "base64,"
    idx = text.find(marker)
    if idx == -1:
        return None
    b64_start = text[idx + len(marker):]
    m = re.match(r"[A-Za-z0-9+/=\n\r]+", b64_start)
    if not m:
        return None
    return base64.b64decode(m.group())
