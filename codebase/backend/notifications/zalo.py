"""Async client for Zalo Official Account and ZBS template notifications."""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from codebase.backend.config import Settings, get_settings


class ZaloConfigurationError(RuntimeError):
    pass


class ZaloAPIError(RuntimeError):
    pass


@dataclass(frozen=True)
class ZaloSendResult:
    channel: str
    tracking_id: str | None


class ZaloNotifier:
    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport

    async def _post(self, url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.zalo_notifications_enabled:
            raise ZaloConfigurationError("Zalo notifications are disabled")
        if not token:
            raise ZaloConfigurationError("Zalo access token is not configured")

        headers = {"access_token": token, "Content-Type": "application/json"}
        attempts = self.settings.zalo_max_retries + 1
        async with httpx.AsyncClient(
            timeout=self.settings.zalo_timeout_seconds,
            transport=self.transport,
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < attempts:
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    response.raise_for_status()
                    body = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise ZaloAPIError(f"Zalo request failed: {type(exc).__name__}") from exc

                error_code = body.get("error", body.get("error_code", 0))
                if str(error_code) not in {"0", "None", ""}:
                    message = body.get("message") or body.get("error_name") or "Unknown Zalo API error"
                    raise ZaloAPIError(f"Zalo API rejected the message ({error_code}): {message}")
                return body

        raise ZaloAPIError("Zalo request failed after retries")

    async def send_oa_text(self, user_id: str, text: str) -> ZaloSendResult:
        body = await self._post(
            self.settings.zalo_oa_api_url,
            self.settings.zalo_oa_access_token,
            {"recipient": {"user_id": user_id}, "message": {"text": text}},
        )
        message_id = (body.get("data") or {}).get("message_id")
        return ZaloSendResult(channel="oa", tracking_id=str(message_id) if message_id else None)

    async def send_zbs_template(
        self,
        recipient_id: str,
        template_id: str,
        template_data: dict[str, Any],
        tracking_id: str | None = None,
    ) -> ZaloSendResult:
        payload: dict[str, Any] = {
            "phone": recipient_id,
            "template_id": template_id,
            "template_data": template_data,
        }
        if tracking_id:
            payload["tracking_id"] = tracking_id
        body = await self._post(
            self.settings.zalo_zbs_api_url,
            self.settings.zalo_zbs_access_token,
            payload,
        )
        message_id = (body.get("data") or {}).get("msg_id") or tracking_id
        return ZaloSendResult(channel="zbs", tracking_id=str(message_id) if message_id else None)


class ZaloBotNotifier:
    """Client for the Zalo Bot Platform (bot-api.zaloplatforms.com).

    A separate product from Zalo OA above: the bot token is embedded in the
    URL path rather than sent as an access_token header, and recipients are
    addressed by chat_id (obtained via getUpdates/webhook once a user has
    messaged the bot) rather than an OA-linked user_id.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.transport = transport

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.zalo_bot_enabled:
            raise ZaloConfigurationError("Zalo Bot notifications are disabled")
        if not self.settings.zalo_bot_token:
            raise ZaloConfigurationError("Zalo Bot token is not configured")

        url = f"{self.settings.zalo_bot_api_base}/bot{self.settings.zalo_bot_token}/{method}"
        attempts = self.settings.zalo_max_retries + 1
        async with httpx.AsyncClient(
            timeout=self.settings.zalo_timeout_seconds,
            transport=self.transport,
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(url, json=payload)
                    if (response.status_code == 429 or response.status_code >= 500) and attempt + 1 < attempts:
                        await asyncio.sleep(min(2**attempt, 4))
                        continue
                    response.raise_for_status()
                    body = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise ZaloAPIError(f"Zalo Bot request failed: {type(exc).__name__}") from exc

                if not body.get("ok"):
                    message = body.get("description") or "Unknown Zalo Bot API error"
                    raise ZaloAPIError(f"Zalo Bot API rejected the message: {message}")
                return body

        raise ZaloAPIError("Zalo Bot request failed after retries")

    async def send_message(self, chat_id: str, text: str) -> ZaloSendResult:
        body = await self._call("sendMessage", {"chat_id": chat_id, "text": text})
        message_id = (body.get("result") or {}).get("message_id")
        return ZaloSendResult(channel="bot", tracking_id=str(message_id) if message_id else None)

    async def get_updates(self, *, timeout: int = 20) -> dict[str, Any]:
        """Long-poll for the next pending inbound message. Unlike `_call`, does
        not raise on `ok: false` — a 408 "Request timeout" body is the expected
        shape when nothing arrived within `timeout` seconds, not a failure the
        caller should treat as one; the caller decides what `ok: false` means
        for its own retry/backoff policy.
        """
        if not self.settings.zalo_bot_enabled:
            raise ZaloConfigurationError("Zalo Bot notifications are disabled")
        if not self.settings.zalo_bot_token:
            raise ZaloConfigurationError("Zalo Bot token is not configured")

        url = f"{self.settings.zalo_bot_api_base}/bot{self.settings.zalo_bot_token}/getUpdates"
        # The client-side timeout must exceed the long-poll `timeout` itself —
        # the server intentionally holds the connection open for up to that
        # long waiting for a message before responding.
        async with httpx.AsyncClient(timeout=timeout + 10, transport=self.transport) as client:
            try:
                response = await client.post(url, json={"timeout": timeout})
                response.raise_for_status()
                return dict(response.json())
            except (httpx.HTTPError, ValueError) as exc:
                raise ZaloAPIError(f"Zalo Bot getUpdates failed: {type(exc).__name__}") from exc
