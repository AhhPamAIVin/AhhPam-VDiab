# Zalo notifications

The backend exposes `POST /api/v1/notifications/zalo` for authenticated admins and doctors. It sends one notification to a doctor or patient per request.

- `channel=oa`: plain text to a Zalo OA user ID, subject to Zalo OA messaging policies. Requires an Official Account, which must be approved/created by Zalo — not available in dev without one.
- `channel=zbs`: an approved ZBS template to a phone number, intended for proactive transactional reminders. Also requires an OA.
- `channel=bot`: plain text to a `chat_id` via the [Zalo Bot Platform](https://bot.zaloplatforms.com/docs/build-your-bot/) — a separate product from Zalo OA. Created instantly at [zalo.me/s/botcreator](https://zalo.me/s/botcreator/) with no approval wait, so this is the channel to use when an OA isn't available yet.

## Configuration

Copy `.env.example` to `.env`, then configure:

```env
ZALO_NOTIFICATIONS_ENABLED=true
ZALO_OA_ACCESS_TOKEN=your-oa-token
ZALO_ZBS_ACCESS_TOKEN=your-zbs-token

ZALO_BOT_ENABLED=true
ZALO_BOT_TOKEN=your-bot-token
```

Never commit `.env`, recipient phone numbers, Zalo UIDs, or medical details. Store the mapping from internal doctor/patient IDs to recipients in the protected database and obtain user consent.

## OA request

```json
{
  "recipient_type": "patient",
  "recipient_id": "ZALO_OA_USER_ID",
  "channel": "oa",
  "text": "Bạn có một nhắc nhở mới. Vui lòng mở ứng dụng để xem chi tiết."
}
```

## ZBS request

```json
{
  "recipient_type": "doctor",
  "recipient_id": "84901234567",
  "channel": "zbs",
  "template_id": "APPROVED_TEMPLATE_ID",
  "template_data": {"name": "Bác sĩ An", "time": "08:30 20/08/2026"},
  "tracking_id": "appointment-123"
}
```

## Bot request

```json
{
  "recipient_type": "patient",
  "recipient_id": "CHAT_ID",
  "channel": "bot",
  "text": "Bạn có một nhắc nhở mới. Vui lòng mở ứng dụng để xem chi tiết."
}
```

`recipient_id` here is a `chat_id`, not a phone number or OA user_id. The bot only learns a user's `chat_id` after that user messages the bot directly at least once; there's no lookup-by-phone or lookup-by-name API.

Send the normal JWT access token in the `Authorization: Bearer ...` header.

## Connecting a patient's chat_id (in-app flow)

A patient links their own Zalo chat from the notifications page's "Kết nối Zalo" button — no manual admin step required:

1. `POST /api/v1/notifications/zalo/link-code` (patient JWT) issues a short single-use code (`ZaloLinkCode`, `ZALO_LINK_CODE_TTL_MINUTES` default 10) and returns it along with `ZALO_BOT_CHAT_URL`.
2. The patient opens that link in Zalo and sends the code as a plain message to the bot.
3. `notifications/zalo_link_listener.py` — a background task started in `main.py`'s lifespan alongside the reminder scheduler — long-polls `getUpdates` continuously (not just on demand) and matches each inbound message's text against outstanding codes (`notifications/service.py`'s `consume_link_code`). A match sets that user's `zalo_chat_id` and replies with a confirmation message.
4. The frontend polls `GET /api/v1/notifications/zalo/link-status` while the modal is open to detect the link and close automatically.

This listener is the sole consumer of `getUpdates` — a manual `getUpdates` poll from elsewhere (e.g. curl, during debugging) will race it and can steal a pending message before the listener sees it, since Zalo's `getUpdates` appears to hand each pending message to whichever caller polls next rather than supporting per-consumer offsets.
