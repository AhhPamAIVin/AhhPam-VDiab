import json

import httpx
import pytest

from codebase.backend.config import Settings
from codebase.backend.notifications.zalo import ZaloBotNotifier, ZaloConfigurationError, ZaloNotifier


@pytest.mark.asyncio
async def test_send_oa_text_builds_expected_request():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"error": 0, "data": {"message_id": "msg-1"}})

    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-secret",
        zalo_notifications_enabled=True,
        zalo_oa_access_token="fake-test-token",
    )
    notifier = ZaloNotifier(settings, transport=httpx.MockTransport(handler))
    result = await notifier.send_oa_text("uid-1", "Xin chào")

    assert captured["headers"]["access_token"] == "fake-test-token"
    assert captured["body"] == {"recipient": {"user_id": "uid-1"}, "message": {"text": "Xin chào"}}
    assert result.tracking_id == "msg-1"


@pytest.mark.asyncio
async def test_disabled_notifications_do_not_call_zalo():
    settings = Settings(_env_file=None, jwt_secret_key="test-secret", zalo_notifications_enabled=False)
    notifier = ZaloNotifier(settings)
    with pytest.raises(ZaloConfigurationError):
        await notifier.send_oa_text("uid-1", "Xin chào")


@pytest.mark.asyncio
async def test_bot_send_message_builds_expected_request():
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": "msg-1"}})

    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-secret",
        zalo_bot_enabled=True,
        zalo_bot_token="fake-bot-token",
    )
    notifier = ZaloBotNotifier(settings, transport=httpx.MockTransport(handler))
    result = await notifier.send_message("chat-1", "Xin chào")

    assert captured["url"].endswith("/botfake-bot-token/sendMessage")
    assert captured["body"] == {"chat_id": "chat-1", "text": "Xin chào"}
    assert result.tracking_id == "msg-1"


@pytest.mark.asyncio
async def test_disabled_bot_notifications_do_not_call_zalo():
    settings = Settings(_env_file=None, jwt_secret_key="test-secret", zalo_bot_enabled=False)
    notifier = ZaloBotNotifier(settings)
    with pytest.raises(ZaloConfigurationError):
        await notifier.send_message("chat-1", "Xin chào")
