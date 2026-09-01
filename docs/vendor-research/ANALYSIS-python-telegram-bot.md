# python-telegram-bot: Verification for Tapestry (Telegram surface)

Source: `git clone --depth 1 https://github.com/python-telegram-bot/python-telegram-bot`
Commit: `308a03b4cdadb4daaa011f2e831217d0d818d886` (2026-09-01)
Library version: **22.8.0** (`src/telegram/_version.py`)
Targeted Telegram Bot API version: **10.0** (`src/telegram/constants.py:187`, `BOT_API_VERSION_INFO = _BotAPIVersion(major=10, minor=0)`; also stated in `README.rst:80`)

## Summary

- **License is NOT MIT. It is LGPL-3.0-only.** `pyproject.toml` declares `license = "LGPL-3.0-only"`. The repo root has three license files (`LICENSE` = plain GPLv3 text, `LICENSE.lesser` = LGPLv3 text, `LICENSE.dual` = GPLv3+LGPLv3 concatenated with a "choose either" notice), but the license that actually governs the package as distributed on PyPI is LGPLv3, confirmed by `pyproject.toml`'s `license` field, its trove classifier, and `README.rst`'s own "License" section. This has real consequences for a commercial product — see the License section below.
- **No per-message username/avatar override exists** for a normal group bot, confirmed by reading the full `send_message` signature and every "identity-ish" field in the SDK (`sender_chat`, `business_connection_id`, `author_signature`). Distinct visible personas in one group genuinely require either separate bot accounts/tokens, or a workaround (name-prefixing, or one bot per persona attached to per-persona Telegram User/Business accounts). The original assumption was correct.
- **Forum/topics ARE fully supported** (`create_forum_topic`, `message_thread_id`, `Chat.is_forum`) and are a solid candidate for a "thread panel" equivalent.
- **Inline keyboards + callback queries are first-class** and exactly fit an Approve/Reject control, via `InlineKeyboardMarkup`/`InlineKeyboardButton` + `CallbackQueryHandler`.
- **Both webhook and long-polling are supported**; the library's own examples and docstrings recommend running it *inside* your own asyncio web server/service (not using the blocking `run_polling`/`run_webhook` convenience wrappers) when integrating with another asyncio-based system — directly relevant to embedding this alongside a LangGraph loop.
- **Fully asyncio-native** (`async def` throughout `telegram.Bot`, `Application` is an `asyncio` orchestrator with an `update_queue`).

---

## 1. Per-message identity override (Discord-webhook equivalent)

**Verdict: No such mechanism exists for bots posting into a normal group.** Checked the actual `Bot.send_message` signature in `src/telegram/_bot.py:1015-1039`:

```python
async def send_message(
    self,
    chat_id: int | str,
    text: str,
    parse_mode: ODVInput[str] = DEFAULT_NONE,
    entities: Sequence["MessageEntity"] | None = None,
    disable_notification: ODVInput[bool] = DEFAULT_NONE,
    protect_content: ODVInput[bool] = DEFAULT_NONE,
    reply_markup: "ReplyMarkup | None" = None,
    message_thread_id: int | None = None,
    link_preview_options: ODVInput["LinkPreviewOptions"] = DEFAULT_NONE,
    reply_parameters: "ReplyParameters | None" = None,
    business_connection_id: str | None = None,
    message_effect_id: str | None = None,
    allow_paid_broadcast: bool | None = None,
    direct_messages_topic_id: int | None = None,
    suggested_post_parameters: "SuggestedPostParameters | None" = None,
    *,
    ...
) -> Message:
```

There is no `username`, `display_name`, or `avatar_url` parameter, and none exists anywhere else in the `Bot` class either (grepped for `author_signature`, `display_name`, `override_name`, `username_override` across `src/telegram/*.py`). I specifically checked the three fields that could plausibly do this job, and all three fail to qualify:

- **`sender_chat`** (`src/telegram/_message.py:308-312`): this is a **read-only field on incoming `Message` objects**, not a `send_message` parameter. It's populated automatically by Telegram only when a message came from "an anonymous administrator" of a supergroup or a linked discussion-group forward — it describes who *actually* sent something, it is not something a bot can set to spoof a name. Not usable.
- **`business_connection_id`** (present on `send_message`, `edit_message_text`, etc. — 167 matches across `_bot.py`): this is Telegram's 2023+ "Telegram Business" feature. It lets a bot send messages **on behalf of a real Telegram user's personal Business account** that has explicitly connected that bot via Telegram Settings → Business → Chatbots. It does *not* let a bot invent an arbitrary named persona — the visible identity is still a real, distinct Telegram account (the business owner's), one bot-per-account-connection, and it's designed for 1 bot ↔ 1 connected business account (e.g., a shop's auto-reply), not N synthetic personas in a group you control. Doesn't solve the problem, and still implies "one real account per identity."
- **`author_signature`** (`src/telegram/_message.py:470`, `922`): incoming-only field, applies only to **channel posts** where the channel has "Sign messages" enabled, and the signature is the *channel admin's own configured name*, not a free-form per-message override. Also not present as a `send_message` parameter. Not applicable to groups.

**Conclusion:** the original assumption holds. Distinct visible per-message identities in one Telegram group require either (a) one real bot account/token per persona, or (b) a single bot with name-prefixed / formatted text (and optionally a custom emoji or a small icon/sigil in the text) to fake a "role label." Nothing in Bot API 10.0 (the version this library targets) or in the library's surface changes that.

## 2. Forum/topics mode

**Verdict: Fully supported**, and it's a good candidate for a thread-panel equivalent.

- `telegram.Chat.is_forum: bool | None` (`src/telegram/_chat.py:98,113,127`) tells you if a supergroup has Topics enabled.
- `Message.message_thread_id: int | None` (`src/telegram/_message.py:1266,1376,1504`) is present on every message and identifies which topic it belongs to; `Message.is_topic_message` is also available (`src/telegram/_message.py:1608`).
- `Bot` has a full topic-management API (`src/telegram/_bot.py:8685-9164`):
  - `create_forum_topic(chat_id, name, icon_color=None, icon_custom_emoji_id=None)` → returns a `ForumTopic` (with `message_thread_id`). Requires the bot to be an admin with `can_manage_topics`.
  - `edit_forum_topic`, `close_forum_topic`, `reopen_forum_topic`, `delete_forum_topic`, `unpin_all_forum_topic_messages`
  - `edit_general_forum_topic`, `close_general_forum_topic`, `reopen_general_forum_topic`, `hide_general_forum_topic`, `unhide_general_forum_topic`
  - `get_forum_topic_icon_stickers()` to enumerate valid icon emoji.
- `send_message` (and all other send_* methods) accept `message_thread_id` directly to post into a specific topic.

Example `create_forum_topic` (`src/telegram/_bot.py:8712-8726`):

```python
async def create_forum_topic(
    self,
    chat_id: str | int,
    name: str,
    icon_color: int | None = None,
    icon_custom_emoji_id: str | None = None,
    ...
) -> ForumTopic:
```

**Implication for Tapestry:** one Telegram supergroup with Topics enabled maps naturally onto "one thread per task/conversation," with each agent persona posting into the relevant topic. This is a legitimate structural analog to a thread panel — though it's still orthogonal to the persona-identity question in §1 (topics organize *where* messages go, not *who* they visually appear from).

## 3. Inline keyboards (Approve/Reject)

**Verdict: first-class, exactly fits the use case.** Verified in `src/telegram/_inline/inlinekeyboardbutton.py` and `src/telegram/_inline/inlinekeyboardmarkup.py`, and demonstrated end-to-end in the library's own example `examples/inlinekeyboard.py`.

`InlineKeyboardButton.__init__` (`src/telegram/_inline/inlinekeyboardbutton.py:286-303`):
```python
def __init__(
    self,
    text: str,
    url: str | None = None,
    callback_data: str | object | None = None,
    ...
): ...
```

`InlineKeyboardMarkup.__init__` (`src/telegram/_inline/inlinekeyboardmarkup.py:69-84`):
```python
def __init__(
    self,
    inline_keyboard: Sequence[Sequence[InlineKeyboardButton]],
    ...
): ...
```

`CallbackQueryHandler` (`src/telegram/ext/_handlers/callbackqueryhandler.py:133-140`), the handler that receives the button press:
```python
def __init__(
    self: "CallbackQueryHandler[CCT, RT]",
    callback: HandlerCallback[Update, CCT, RT],
    pattern: str | Pattern[str] | type | Callable[[object], bool] | None = None,
    game_pattern: str | Pattern[str] | None = None,
    block: DVType[bool] = DEFAULT_TRUE,
):
```
Callback signature it expects: `async def callback(update: Update, context: CallbackContext)`.

Official example, verbatim, `examples/inlinekeyboard.py`:
```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("Option 1", callback_data="1"),
         InlineKeyboardButton("Option 2", callback_data="2")],
        [InlineKeyboardButton("Option 3", callback_data="3")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Please choose:", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # callback queries must always be answered
    await query.edit_message_text(text=f"Selected option: {query.data}")

application = Application.builder().token("TOKEN").build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(button))
application.run_polling(allowed_updates=Update.ALL_TYPES)
```
Note the library's own warning in the docstring: `CallbackQuery` must always be answered (`query.answer()`) even if you don't need to show a notification — some Telegram clients misbehave otherwise. The server-side handler is `Bot.answer_callback_query(callback_query_id, text=None, show_alert=None, ...)` (`src/telegram/_bot.py:4306-4319`).

## 4. Update delivery model: webhook vs long-polling

**Verdict: both are fully supported**, and the library explicitly steers you away from its own convenience wrappers for a backend service.

- `Application.run_polling(...)` (`src/telegram/ext/_application.py:742`) and `Application.run_webhook(...)` (`:853`) are both present.
- Both are documented (`docs/source/inclusions/application_run_tip.rst`, included in both methods' docstrings) with this tip, quoted in full:
  > "When combining `python-telegram-bot` with other `asyncio` based frameworks, using this method is likely not the best choice, as it blocks the event loop until it receives a stop signal... Instead, you can manually call the methods listed below to start and shut down the application and the `updater`. Keeping the event loop running and listening for a stop signal is then up to you."
- The library ships **four ready-made "custom webhook" backend integrations** in `examples/customwebhookbot/`: `flaskbot.py`, `djangobot.py`, `quartbot.py`, `starlettebot.py` — this is their own answer to "how do I run this as part of a real backend service." The Starlette one (`examples/customwebhookbot/starlettebot.py`) is the cleanest asyncio-native pattern: it builds the `Application` with `.updater(None)` (no built-in updater, since the webserver itself will feed updates), calls `await application.bot.set_webhook(...)`, and drives the app manually:
  ```python
  application = Application.builder().token(TOKEN).updater(None).build()
  ...
  async def telegram(request: Request) -> Response:
      await application.update_queue.put(Update.de_json(data=await request.json(), bot=application.bot))
      return Response()
  ...
  async with application:
      await application.start()
      await webserver.serve()
      await application.stop()
  ```
  This pattern — pushing raw `Update` objects into `application.update_queue` from your own request handler — is exactly the seam you'd use to run PTB's dispatcher inside a FastAPI/Starlette service that also owns a LangGraph loop, without letting PTB take over the event loop.
- `pip install "python-telegram-bot[webhooks]"` pulls in `tornado~=6.4` as an optional dependency for `Updater.start_webhook`/`Application.run_webhook` (`README.rst:157`) — only needed if you use the *built-in* webhook server rather than your own.

## 5. Async model

**Verdict: confirmed fully `asyncio`-based.** `telegram.Bot` (`src/telegram/_bot.py:180`) is declared as `class Bot(TelegramObject, contextlib.AbstractAsyncContextManager["Bot"])`, and every API call (`send_message`, `get_updates`, `set_webhook`, etc.) is `async def`. `Application` is likewise asyncio-native, exposing an `update_queue: asyncio.Queue` that you can feed directly (see §4).

Relevant to running it alongside a LangGraph-orchestrated agent loop in the same process:
- Don't call `run_polling()`/`run_webhook()` directly if you already own the event loop (e.g., your LangGraph loop or your own web server does) — per the library's own tip in §4, use the manual lifecycle (`async with application: await application.start(); ...; await application.stop()`) and either run PTB's own polling `Updater` alongside it, or push `Update` objects into `application.update_queue` from wherever you already receive them (webhook handler, etc.).
- Because everything is `async def`, PTB coroutines can be awaited directly from LangGraph node functions (assuming those are themselves async, or run via `asyncio.run_coroutine_threadsafe`/an executor if LangGraph's nodes are sync) — no thread-per-library friction, no GIL-bound blocking calls to work around, as long as you keep it one event loop.
- Handlers registered on `Application` (`CommandHandler`, `CallbackQueryHandler`, etc.) run as `asyncio` tasks scheduled on `Application.create_task`, so a slow handler (e.g., one that calls out to a LangGraph agent step) won't necessarily block other Telegram updates unless `block=True` (the default) is set on that handler — worth tuning per-handler if agent turns are slow.

## 6. License — read directly from the repo

Repo root contains **three** license-related files (confirmed by direct `find`/`cat`, not assumed):

| File | Contents |
|---|---|
| `LICENSE` | Full **GNU General Public License v3 (GPLv3)** text |
| `LICENSE.lesser` | Full **GNU Lesser General Public License v3 (LGPLv3)** text |
| `LICENSE.dual` | GPLv3 followed by LGPLv3, with header: *"NOTICE: You can find here the GPLv3 license and after the Lesser GPLv3 license. You may choose either license."* |

The **operative** license — the one that actually governs the distributed package — is LGPLv3, confirmed two more ways:

- `pyproject.toml`:
  ```
  license = "LGPL-3.0-only"
  license-files = ["LICENSE", "LICENSE.dual", "LICENSE.lesser"]
  classifiers = [..., "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)", ...]
  ```
- `README.rst:233-237` ("License" section), verbatim:
  > "You may copy, distribute and modify the software provided that modifications are described and licensed for free under LGPL-3. Derivative works (including modifications or anything statically linked to the library) can only be redistributed under LGPL-3, but applications that use the library don't have to be."

Separately, `examples/LICENSE.txt` (covering everything under `examples/`, including the four webhook-integration examples used in §4) is **CC0 1.0 Universal** (public domain) — confirmed by reading the file header directly.

**What this means concretely for Tapestry:**
- **This is not MIT.** LGPLv3 is a copyleft license, just a weaker ("lesser") one than GPLv3.
- You can `pip install python-telegram-bot` and **use it as a library dependency in a closed-source/proprietary application without that application needing to be open-sourced** — this is exactly the case LGPL is designed for ("applications that use the library don't have to be" LGPL, per the README). Importing it, calling its API, building Tapestry's proprietary backend on top of it: fine, no obligation to release Tapestry's own source.
- The obligation only kicks in if you **modify the library itself** (i.e., patch `python-telegram-bot`'s own source and ship that modified version) or **statically link it into a single combined binary** — in those cases, the modified/combined library code must be redistributed under LGPL-3 (and you must allow users to relink/replace the library, per the LGPL's terms). For a normal Python dependency (installed via pip, imported, not vendored-and-patched), this is not a practical concern.
- If you plan to vendor and patch this library in-tree (which this task's clone-to-`vendor-research/` might suggest as a pattern), be aware that a patched copy must remain LGPL-3-licensed and its modifications disclosed if distributed — don't fold patches into a proprietary bundle without keeping this in mind.
- The example scripts (`examples/`) are CC0 — free to copy verbatim into your own codebase with zero licensing obligation, unlike the core library.

---

## Recommendation

### (a) One bot listening for messages

```python
import logging
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)

async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None or not msg.text:
        return
    # message_thread_id lets you route by "topic" if the group is a forum (see §2)
    thread_id = msg.message_thread_id
    print(f"[chat={update.effective_chat.id} thread={thread_id}] "
          f"{update.effective_user.full_name}: {msg.text}")
    # -> hand off to your LangGraph agent loop here

def main() -> None:
    application = Application.builder().token("BOT_TOKEN").build()
    application.add_handler(
        MessageHandler(filters.TEXT & filters.ChatType.GROUPS, on_group_message)
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
```
For production, prefer the manual-lifecycle / webhook pattern from `examples/customwebhookbot/starlettebot.py` (§4) instead of `run_polling`, so PTB shares the event loop with your own FastAPI/Starlette service and LangGraph loop rather than blocking it.

### (b) Posting a message with an Approve/Reject inline keyboard, and handling the callback

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

async def request_approval(context: ContextTypes.DEFAULT_TYPE, chat_id: int, action_id: str, description: str) -> None:
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Approve", callback_data=f"approve:{action_id}"),
        InlineKeyboardButton("Reject", callback_data=f"reject:{action_id}"),
    ]])
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Agent wants to: {description}\nApprove?",
        reply_markup=keyboard,
    )

async def on_approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # required: ack the callback so the client stops spinning

    decision, action_id = query.data.split(":", 1)
    approved = decision == "approve"

    # -> resume the LangGraph run / release the gated tool call here, keyed on action_id

    await query.edit_message_text(
        text=f"{query.message.text}\n\n"
             f"Decision: {'APPROVED' if approved else 'REJECTED'} by {query.from_user.full_name}"
    )

application = Application.builder().token("BOT_TOKEN").build()
application.add_handler(CallbackQueryHandler(on_approval_callback, pattern=r"^(approve|reject):"))
```
Notes: `query.answer()` is mandatory (per the library's own docstring warning in §3) or some Telegram clients show a stuck loading spinner on the button. `query.from_user` gives you who actually clicked — useful for an audit trail of who approved a risky agent action.

### (c) Honest recommendation for multi-persona representation in one group

Given the confirmed finding in §1 — **there is no Discord-webhook-style per-message identity override in the Telegram Bot API this library targets (10.0), and none is exposed anywhere in `telegram.Bot`** — the practical options, ranked:

1. **One real bot account per persona (recommended if the number of personas is small and fixed, e.g., <=5-10).** Each persona = its own bot token from @BotFather, its own name and avatar, all added as members of the same group. This is the only way to get an authentically distinct Telegram identity (name + avatar + "bot" badge) per message, with zero text hacks. Downside: N bots to provision/manage/rotate credentials for, and Telegram's per-bot admin/rate-limit bookkeeping multiplies by N. This is directly analogous to what you'd already be doing on Discord if you weren't using the webhook trick — so it's not a regression, just a different mechanism to reach the same place.
2. **Single bot + strict name-prefixing/formatting convention, with topics (§2) for structural separation.** E.g., every message starts with a bolded persona tag (`*Researcher:*` via `parse_mode="MarkdownV2"`, or a persona-specific emoji sigil), and each logical conversation/task gets its own forum topic so personas' threads of thought don't interleave chaotically even though they share one visible "From" identity. Cheapest to operate (one token, one rate limit budget), but weakest visual distinction — every message still shows the same bot avatar/name in the Telegram UI chrome, only the text differs.
3. **Hybrid**: a small number of real bot accounts (e.g., 2-3 "tiers" — one for agent chatter, one for system/approval prompts, one for a human-facing "orchestrator" persona) combined with name-prefixing within each tier for finer-grained persona distinction. Balances provisioning overhead against visual clarity better than either pure extreme.

Do **not** rely on `business_connection_id` as a workaround (§1) — it requires a real human's Telegram Business account to have explicitly connected your bot via their own Settings, which is not something you can provision programmatically for N synthetic personas, and it's designed for a single connected account, not a fan-out of identities.

Given that approve/reject gating (§3) and topic-per-task organization (§2) both work cleanly regardless of which persona strategy you pick, the identity question is decoupled from the rest of the integration — you can start with option 2 (cheap, one token) and upgrade to option 1 later per-persona without touching the approval or topic-routing code.
