import asyncio
import logging
from typing import Any

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class OneCCommunicationError(Exception):
    pass


class OrderPayload(BaseModel):
    agent_id: int
    customer_code: str = Field(min_length=1)
    items: list[dict[str, Any]]
    comment: str | None = None


class OneCClient:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        timeout_sec: int = 15,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout_sec = timeout_sec

    async def send_order(self, payload: OrderPayload) -> dict[str, Any]:
        url = f"{self.base_url}/orders"
        timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
        auth = None
        if self.username and self.password:
            auth = aiohttp.BasicAuth(self.username, self.password)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload.model_dump(), auth=auth) as response:
                    body = await response.text()
                    if response.status >= 400:
                        logger.error("1C HTTP error: status=%s body=%s", response.status, body)
                        raise OneCCommunicationError(
                            f"1C returned HTTP {response.status}",
                        )

                    if not body.strip():
                        return {"status": "ok"}

                    try:
                        return await response.json(content_type=None)
                    except aiohttp.ContentTypeError:
                        return {"status": "ok", "raw_response": body}
        except asyncio.TimeoutError as exc:
            logger.exception("1C timeout while sending order")
            raise OneCCommunicationError("1C timeout") from exc
        except aiohttp.ClientError as exc:
            logger.exception("1C unavailable")
            raise OneCCommunicationError("1C unavailable") from exc
