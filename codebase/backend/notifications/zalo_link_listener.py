import asyncio
import logging

from sqlmodel import Session

from codebase.backend.config import get_settings
from codebase.backend.db import engine
from codebase.backend.notifications.service import consume_link_code
from codebase.backend.notifications.zalo import ZaloAPIError, ZaloBotNotifier, ZaloConfigurationError

logger = logging.getLogger(__name__)

_task: asyncio.Task[None] | None = None

# How long each getUpdates long-poll blocks server-side waiting for a message.
# The listener loops immediately back into another call once one returns, so
# this is a latency/request-rate tradeoff, not a fixed interval.
_POLL_TIMEOUT_SECONDS = 20

# Backoff after a failed poll (network error, misconfiguration) so a broken
# connection doesn't spin the loop as fast as possible against Zalo's API.
_ERROR_BACKOFF_SECONDS = 5


async def _handle_update(notifier: ZaloBotNotifier, body: dict) -> None:
    """One getUpdates response body. `ok: false` (e.g. error_code 408 "Request
    timeout") is the expected shape when nothing arrived within the poll
    window — not a failure, just "poll again" — so it's handled here rather
    than raised as an error by the caller.
    """
    if not body.get("ok"):
        return

    message = (body.get("result") or {}).get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return

    with Session(engine) as session:
        linked = consume_link_code(session, code=text, chat_id=chat_id)

    if not linked:
        return
    try:
        await notifier.send_message(
            chat_id, "Đã liên kết tài khoản Zalo thành công! Bạn sẽ nhận được nhắc nhở qua đây."
        )
    except (ZaloConfigurationError, ZaloAPIError):
        # The link itself already succeeded (committed above) — a failed
        # confirmation reply must not undo it or crash the listener loop.
        pass


async def _listen_forever() -> None:
    notifier = ZaloBotNotifier()
    while True:
        try:
            body = await notifier.get_updates(timeout=_POLL_TIMEOUT_SECONDS)
        except (ZaloConfigurationError, ZaloAPIError) as exc:
            logger.warning("Zalo link listener: %s", exc)
            await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
            continue
        except Exception:
            logger.exception("Zalo link listener: unexpected error polling getUpdates")
            await asyncio.sleep(_ERROR_BACKOFF_SECONDS)
            continue

        try:
            await _handle_update(notifier, body)
        except Exception:
            # A single malformed/unexpected update must never take down the
            # long-poll loop — every other patient's linking still depends on it.
            logger.exception("Zalo link listener: error handling an update")


def start_zalo_link_listener() -> None:
    """Start the background task that redeems Zalo link codes (see
    notifications/service.py's create_link_code/consume_link_code) as patients
    send them to the bot. A no-op when the bot isn't configured, or if already
    running — safe to call more than once, same as notifications/scheduler.py's
    start_scheduler.
    """
    global _task
    if not get_settings().zalo_bot_enabled:
        return
    if _task is not None:
        return
    _task = asyncio.create_task(_listen_forever())


def stop_zalo_link_listener() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
