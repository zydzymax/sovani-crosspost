"""
Midjourney image generation via GoAPI.ai proxy.
Docs: https://www.goapi.ai/docs/midjourney-api
"""

import asyncio
from dataclasses import dataclass
from enum import Enum

import httpx

from ..core.config import settings
from ..core.logging import get_logger

logger = get_logger("services.image_gen_midjourney")


class MidjourneyMode(str, Enum):
    FAST = "fast"
    RELAX = "relax"
    TURBO = "turbo"


@dataclass
class MidjourneyResult:
    success: bool
    image_url: str | None = None
    image_urls: list[str] | None = None  # All 4 variations
    task_id: str | None = None
    error: str | None = None
    cost_estimate: float = 0.0


class MidjourneyProvider:
    """Midjourney via GoAPI.ai"""

    BASE_URL = "https://api.goapi.ai/mj/v2"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.GOAPI_KEY
        if not self.api_key:
            raise ValueError("GOAPI_KEY is required for Midjourney")

        self.client = httpx.AsyncClient(
            timeout=180.0, headers={"X-API-Key": self.api_key, "Content-Type": "application/json"}
        )

    async def imagine(
        self,
        prompt: str,
        aspect_ratio: str = "1:1",
        mode: MidjourneyMode = MidjourneyMode.FAST,
        webhook_url: str | None = None,
    ) -> MidjourneyResult:
        """
        Generate image with Midjourney.

        Args:
            prompt: Image description
            aspect_ratio: "1:1", "16:9", "9:16", "4:3", etc.
            mode: fast (~30s), relax (~2min), turbo (~15s)
            webhook_url: Optional webhook for async notification
        """
        try:
            payload = {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "process_mode": mode.value,
            }

            if webhook_url:
                payload["webhook_url"] = webhook_url
                payload["webhook_type"] = "result"

            logger.info("Starting Midjourney generation", prompt_preview=prompt[:50])

            resp = await self.client.post(f"{self.BASE_URL}/imagine", json=payload)
            resp.raise_for_status()
            data = resp.json()

            task_id = data.get("task_id")
            if not task_id:
                return MidjourneyResult(success=False, error=f"No task_id in response: {data}")

            logger.info("Midjourney task created", task_id=task_id)

            # Poll for result
            result = await self._wait_for_result(task_id)
            return result

        except httpx.HTTPStatusError as e:
            error_msg = f"GoAPI HTTP error: {e.response.status_code} - {e.response.text}"
            logger.error(error_msg)
            return MidjourneyResult(success=False, error=error_msg)
        except Exception as e:
            logger.error("Midjourney generation failed", error=str(e))
            return MidjourneyResult(success=False, error=str(e))

    async def _wait_for_result(self, task_id: str, max_attempts: int = 60, poll_interval: int = 5) -> MidjourneyResult:
        """Poll for task completion."""
        for attempt in range(max_attempts):
            await asyncio.sleep(poll_interval)

            try:
                resp = await self.client.post(f"{self.BASE_URL}/fetch", json={"task_id": task_id})
                resp.raise_for_status()
                data = resp.json()

                status = data.get("status")
                logger.debug("Midjourney task status", task_id=task_id, status=status, attempt=attempt + 1)

                if status == "completed":
                    image_url = data.get("task_result", {}).get("image_url")
                    image_urls = data.get("task_result", {}).get("image_urls", [])

                    return MidjourneyResult(
                        success=True,
                        image_url=image_url,
                        image_urls=image_urls,
                        task_id=task_id,
                        cost_estimate=self._estimate_cost(data),
                    )

                elif status == "failed":
                    error = data.get("task_result", {}).get("error_message", "Unknown error")
                    return MidjourneyResult(success=False, error=error, task_id=task_id)

                elif status in ["pending", "processing"]:
                    continue
                else:
                    logger.warning("Unknown Midjourney task status", task_id=task_id, status=status)

            except Exception as e:
                logger.warning("Midjourney poll error", task_id=task_id, attempt=attempt + 1, error=str(e))
                continue

        return MidjourneyResult(success=False, error="Timeout waiting for Midjourney result", task_id=task_id)

    async def upscale(self, task_id: str, index: int) -> MidjourneyResult:  # 1-4
        """Upscale one of the 4 generated images."""
        return await self._run_action("upscale", task_id, index)

    async def variation(self, task_id: str, index: int) -> MidjourneyResult:  # 1-4
        """Create variations of one image."""
        return await self._run_action("variation", task_id, index)

    async def _run_action(self, action: str, task_id: str, index: int) -> MidjourneyResult:
        try:
            resp = await self.client.post(
                f"{self.BASE_URL}/{action}", json={"origin_task_id": task_id, "index": str(index)}
            )
            resp.raise_for_status()
            data = resp.json()

            new_task_id = data.get("task_id")
            if not new_task_id:
                return MidjourneyResult(success=False, error=f"No task_id returned for {action}")
            return await self._wait_for_result(new_task_id)

        except Exception as e:
            logger.error(f"{action.capitalize()} failed", task_id=task_id, index=index, error=str(e))
            return MidjourneyResult(success=False, error=str(e))

    def _estimate_cost(self, data: dict) -> float:
        """Estimate cost based on mode."""
        mode = data.get("meta", {}).get("process_mode", "fast")
        costs = {"turbo": 0.06, "fast": 0.03, "relax": 0.01}
        return costs.get(mode, 0.03)

    async def close(self):
        await self.client.aclose()


# Singleton instance
_midjourney_provider: MidjourneyProvider | None = None


def get_midjourney_provider() -> MidjourneyProvider:
    global _midjourney_provider
    if _midjourney_provider is None:
        _midjourney_provider = MidjourneyProvider()
    return _midjourney_provider
