import logging
import asyncio
import json
import os
import time
from telegram.error import NetworkError
from telegram import InputMediaPhoto, InputMediaVideo
from telegram import InputMediaPhoto, InputMediaVideo, Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    ConversationHandler,
    filters,
)
from collections import defaultdict
from typing import Dict, List, Any

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    BOT_TOKEN = input("Введите токен вашего Telegram-бота: ").strip()
if not BOT_TOKEN:
    print("Токен не указан. Завершаю работу.")
    exit(1)

CONFIG_FILE = "forward_config.json"
MESSAGE_LOG_FILE = "forward_log.json"

SELECT_ACTION, ADD_SOURCE, ADD_TARGETS, SET_DELAY, DELETE_MESSAGE, PIN_MESSAGE, UNPIN_MESSAGE = range(7)

media_groups: Dict[str, List] = defaultdict(list)
media_group_times: Dict[str, float] = {}
MEDIA_GROUP_TIMEOUT = 300.0


def load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_config():
    return load_json(CONFIG_FILE)


def save_config(config):
    save_json(CONFIG_FILE, config)


def load_log():
    return load_json(MESSAGE_LOG_FILE)


def save_log(log):
    save_json(MESSAGE_LOG_FILE, log)


def main_menu_keyboard():
    keyboard = [
        [KeyboardButton("Добавить источник")],
        [KeyboardButton("Добавить цели к источнику")],
        [KeyboardButton("Настроить задержку репоста")],
        [KeyboardButton("Удалить пересланное сообщение")],
        [KeyboardButton("Закрепить сообщение")],
        [KeyboardButton("Открепить сообщение")],
        [KeyboardButton("Проверить права бота")],
        [KeyboardButton("Текущие настройки")],
        [KeyboardButton("Очистить настройки")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def normalize_chat_for_api(target):
    try:
        return int(target)
    except Exception:
        return target


async def safe_polling(application):
    while True:
        try:
            await application.run_polling()
        except NetworkError:
            await asyncio.sleep(5)


async def process_media_group(group_id, context, target_chats, source_chat_id, delay=0):
    try:
        if group_id not in media_groups:
            return

        messages = media_groups.pop(group_id, [])
        media_group_times.pop(group_id, None)

        if not messages:
            return

        if delay > 0:
            logger.info(f" Медиагруппа {group_id} из {source_chat_id} будет отправлена через {delay} сек")
            print(f"[INFO] Альбом из источника {source_chat_id} будет переслан через {delay} сек")
            await asyncio.sleep(delay)

        messages_sorted = sorted(messages, key=lambda m: m.message_id)

        log_data = load_log()
        group_log_key = f"{source_chat_id}_{messages_sorted[0].message_id}"
        log_data[group_log_key] = []

        for target in target_chats:
            target_for_api = normalize_chat_for_api(target)
            try:
                sent_messages = []
                for message in messages_sorted:
                    if message.photo:
                        sent_msg = await context.bot.copy_message(
                            chat_id=target_for_api,
                            from_chat_id=message.chat_id,
                            message_id=message.message_id
                        )
                    else:
                        sent_msg = await message.forward(chat_id=target_for_api)

                    sent_messages.append(sent_msg)
                    await asyncio.sleep(0.1)

                for sent_msg in sent_messages:
                    log_data[group_log_key].append({"chat": target_for_api, "msg_id": sent_msg.message_id})

                print(f"[INFO] Альбом из {len(messages_sorted)} медиа обработан для {target}")

            except Exception as e:
                logger.error(f"Ошибка отправки медиагруппы в {target}: {e}")

        save_log(log_data)

    except Exception as e:
        logger.exception("Ошибка в process_media_group: %s", e)




async def cleanup_old_media_groups():
    while True:
        try:
            current_time = time.time()
            # Увеличьте время до 10 минут (600 секунд)
            expired = [gid for gid, t in media_group_times.items()
                      if current_time - t > 600]  # 10 минут вместо 12 секунд
            for gid in expired:
                media_groups.pop(gid, None)
                media_group_times.pop(gid, None)
            await asyncio.sleep(30)
        except Exception as e:
            logger.exception("Ошибка cleanup_old_media_groups: %s", e)
            await asyncio.sleep(30)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я бот-репостер.\n\n"
        "Теперь я умею удалять, закреплять и откреплять пересланные сообщения.\n\n"
        "Команды:\n"
        "• Добавить источник\n"
        "• Добавить цели\n"
        "• Настроить задержку\n"
        "• Удалить пересланное сообщение\n"
        "• Закрепить сообщение\n"
        "• Открепить сообщение\n"
        "• Проверить права бота",
        reply_markup=main_menu_keyboard()
    )
    return SELECT_ACTION


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    config = load_config()

    if text == "Добавить источник":
        await update.message.reply_text("Перешли сообщение из источника или укажи его @username/ID:",
                                        reply_markup=ReplyKeyboardRemove())
        return ADD_SOURCE

    if text == "Добавить цели к источнику":
        if not config:
            await update.message.reply_text("Сначала добавь источник!", reply_markup=main_menu_keyboard())
            return SELECT_ACTION
        await update.message.reply_text("Перешли сообщение из источника, потом укажи цели:",
                                        reply_markup=ReplyKeyboardRemove())
        return ADD_TARGETS

    if text == "Настроить задержку репоста":
        if not config:
            await update.message.reply_text("Сначала добавь источник!", reply_markup=main_menu_keyboard())
            return SELECT_ACTION
        await update.message.reply_text("Перешли сообщение из источника, для которого установить задержку:",
                                        reply_markup=ReplyKeyboardRemove())
        return SET_DELAY

    if text == "Удалить пересланное сообщение":
        await update.message.reply_text(
            "Перешли то сообщение из источника, которое было переслано, чтобы удалить его из всех чатов.",
            reply_markup=ReplyKeyboardRemove())
        return DELETE_MESSAGE

    if text == "Закрепить сообщение":
        await update.message.reply_text(
            "Перешли то сообщение из источника, которое было переслано, чтобы закрепить его во всех чатах.",
            reply_markup=ReplyKeyboardRemove())
        return PIN_MESSAGE

    if text == "Открепить сообщение":
        await update.message.reply_text(
            "Перешли то сообщение из источника, которое было переслано, чтобы открепить его во всех чатах.",
            reply_markup=ReplyKeyboardRemove())
        return UNPIN_MESSAGE

    if text == "Проверить права бота":
        return await check_bot_permissions(update, context)

    if text == "Текущие настройки":
        if not config:
            await update.message.reply_text("Настроек нет.", reply_markup=main_menu_keyboard())
            return SELECT_ACTION
        s = "Текущие настройки:\n\n"
        for src, d in config.items():
            if isinstance(d, list):
                targets = d
                delay = 0
            else:
                targets = d.get("targets", [])
                delay = d.get("delay", 0)
            s += f"Источник: `{src}` — {len(targets)} целей, задержка: {delay} сек\n"
        await update.message.reply_text(s, parse_mode="Markdown", reply_markup=main_menu_keyboard())
        return SELECT_ACTION

    if text == "Очистить настройки":
        save_config({})
        await update.message.reply_text("Настройки очищены.", reply_markup=main_menu_keyboard())
        return SELECT_ACTION

    await update.message.reply_text("Неизвестная команда.", reply_markup=main_menu_keyboard())
    return SELECT_ACTION


async def add_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    source_chat_id = None
    if getattr(msg, "forward_from_chat", None):
        source_chat_id = str(msg.forward_from_chat.id)
    else:
        text = (msg.text or "").strip()
        if text.startswith("@") or text.startswith("-") or text.isdigit():
            source_chat_id = text
    if not source_chat_id:
        await update.message.reply_text("Неверный формат.")
        return ADD_SOURCE

    config = load_config()
    config[source_chat_id] = config.get(source_chat_id, {"targets": [], "delay": 0})
    save_config(config)
    await update.message.reply_text(f"Источник добавлен: `{source_chat_id}`", parse_mode="Markdown",
                                    reply_markup=main_menu_keyboard())
    return SELECT_ACTION


async def add_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    config = load_config()
    if getattr(msg, "forward_from_chat", None):
        source_chat_id = str(msg.forward_from_chat.id)
        if source_chat_id not in config:
            await update.message.reply_text("Источник не найден.")
            return SELECT_ACTION
        context.user_data["current_source"] = source_chat_id
        await update.message.reply_text("Введи цели через запятую:")
        return ADD_TARGETS

    source_chat_id = context.user_data.get("current_source")
    if not source_chat_id:
        await update.message.reply_text("Сначала выбери источник.")
        return SELECT_ACTION

    targets = [t.strip() for t in (msg.text or "").split(",") if t.strip()]
    conf_entry = config[source_chat_id]
    conf_entry["targets"].extend([t for t in targets if t not in conf_entry["targets"]])
    save_config(config)
    await update.message.reply_text(f"Добавлены цели. Всего: {len(conf_entry['targets'])}",
                                    reply_markup=main_menu_keyboard())
    context.user_data.pop("current_source", None)
    return SELECT_ACTION


async def set_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    config = load_config()
    if getattr(msg, "forward_from_chat", None):
        src = str(msg.forward_from_chat.id)
        if src not in config:
            await update.message.reply_text("Источник не найден.")
            return SELECT_ACTION
        context.user_data["current_source"] = src
        await update.message.reply_text("Введи задержку в секундах:")
        return SET_DELAY

    src = context.user_data.get("current_source")
    if not src:
        await update.message.reply_text("Сначала выбери источник.")
        return SELECT_ACTION
    delay = int((msg.text or "0").replace("m", "")) * (60 if "m" in msg.text else 1)
    config[src]["delay"] = delay
    save_config(config)
    await update.message.reply_text(f"Задержка {delay} сек установлена.", reply_markup=main_menu_keyboard())
    context.user_data.pop("current_source", None)
    return SELECT_ACTION


async def forward_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    incoming_chat_id = str(chat.id)
    config = load_config()
    if incoming_chat_id not in config:
        return

    entry = config[incoming_chat_id]
    targets = entry.get("targets", [])
    delay = entry.get("delay", 0)
    if not targets:
        return

    # Медиагруппа (альбом)
    if getattr(msg, "media_group_id", None):
        group_id = f"{incoming_chat_id}_{msg.media_group_id}"
        media_groups[group_id].append(msg)
        media_group_times[group_id] = time.time()
        if len(media_groups[group_id]) == 1:
            asyncio.create_task(process_media_group(group_id, context, targets, incoming_chat_id, delay))
        return

    # Одиночное сообщение с задержкой
    if delay > 0:
        logger.info(f"Задержка перед отправкой сообщения из {incoming_chat_id}: {delay} сек")
        print(f"[INFO] Будет отправлено через {delay} сек (источник {incoming_chat_id})")
        await asyncio.sleep(delay)

    log_data = load_log()
    log_data[str(msg.message_id)] = []

    for target in targets:
        try:
            target_api = normalize_chat_for_api(target)

            # ПРОСТАЯ ПЕРЕСЫЛКА ВСЕХ ТИПОВ СООБЩЕНИЙ
            sent_message = await msg.forward(chat_id=target_api)

            if sent_message:
                log_data[str(msg.message_id)].append({"chat": target, "msg_id": sent_message.message_id})

            await asyncio.sleep(0.12)  # Anti-flood

        except Exception as e:
            logger.error(f"Ошибка отправки в {target}: {e}")

    save_log(log_data)




async def delete_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not getattr(msg, "forward_from_chat", None):
        await update.message.reply_text("Перешли то сообщение из источника, которое бот пересылал.")
        return DELETE_MESSAGE

    original_id = str(msg.forward_from_message_id)
    log_data = load_log()
    if original_id not in log_data:
        await update.message.reply_text("Сообщение не найдено в логе пересылок.")
        return SELECT_ACTION

    deleted = 0
    failed_chats = []

    for entry in log_data[original_id]:
        try:
            await context.bot.delete_message(chat_id=normalize_chat_for_api(entry["chat"]), message_id=entry["msg_id"])
            deleted += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Ошибка удаления из {entry['chat']}: {error_msg}")
            failed_chats.append(f"{entry['chat']}: {error_msg}")

    result_message = f"Удалено {deleted} сообщений.\n"
    if failed_chats:
        result_message += f"\nНе удалось удалить в {len(failed_chats)} чатах:\n"
        for i, failed in enumerate(failed_chats[:5], 1):
            result_message += f"{i}. {failed}\n"
        if len(failed_chats) > 5:
            result_message += f"... и еще {len(failed_chats) - 5} чатов\n"

    await update.message.reply_text(result_message, reply_markup=main_menu_keyboard())
    return SELECT_ACTION


async def pin_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not getattr(msg, "forward_from_chat", None):
        await update.message.reply_text("Перешли то сообщение из источника, которое бот пересылал.")
        return PIN_MESSAGE

    original_id = str(msg.forward_from_message_id)
    log_data = load_log()

    target_entries = []

    if original_id in log_data:
        target_entries.extend(log_data[original_id])

    for group_id, entries in log_data.items():
        if group_id.startswith(f"{msg.forward_from_chat.id}_"):
            target_entries.extend(entries)

    if not target_entries:
        await update.message.reply_text("Сообщение не найдено в логе пересылок.")
        return SELECT_ACTION

    pinned = 0
    failed_chats = []

    chat_entries = {}
    for entry in target_entries:
        chat_id = entry["chat"]
        if chat_id not in chat_entries:
            chat_entries[chat_id] = []
        chat_entries[chat_id].append(entry["msg_id"])

    for chat_id, message_ids in chat_entries.items():
        try:
            target_api = normalize_chat_for_api(chat_id)
            chat = await context.bot.get_chat(target_api)
            member = await context.bot.get_chat_member(chat.id, context.bot.id)

            if member.status != "administrator":
                failed_chats.append(f"{chat_id}: Бот не администратор")
                continue

            if not member.can_pin_messages:
                failed_chats.append(f"{chat_id}: Нет права на закрепление")
                continue

            message_id_to_pin = message_ids[0]

            await context.bot.pin_chat_message(
                chat_id=target_api,
                message_id=message_id_to_pin,
                disable_notification=False
            )
            pinned += 1
            await asyncio.sleep(0.1)

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Ошибка закрепления в {chat_id}: {error_msg}")

            if "CHAT_ADMIN_REQUIRED" in error_msg:
                error_msg = "Требуются права администратора"
            elif "not enough rights" in error_msg.lower():
                error_msg = "Недостаточно прав"
            elif "message to pin not found" in error_msg:
                error_msg = "Сообщение не найдено"
            elif "CHAT_WRITE_FORBIDDEN" in error_msg:
                error_msg = "Нет права на отправку сообщений"
            elif "Bad Request: message can't be pinned" in error_msg:
                error_msg = "Сообщение нельзя закрепить"

            failed_chats.append(f"{chat_id}: {error_msg}")

    result_message = f"Закреплено {pinned} сообщений.\n"
    if failed_chats:
        result_message += f"\nНе удалось закрепить в {len(failed_chats)} чатах:\n"
        for i, failed in enumerate(failed_chats[:5], 1):
            result_message += f"{i}. {failed}\n"
        if len(failed_chats) > 5:
            result_message += f"... и еще {len(failed_chats) - 5} чатов\n"

    await update.message.reply_text(result_message, reply_markup=main_menu_keyboard())
    return SELECT_ACTION

async def safe_unpin_messages(context, chat_id, message_ids):
    for msg_id in message_ids:
        while True:
            try:
                await context.bot.unpin_chat_message(chat_id=chat_id, message_id=msg_id)
                print(f"Откреплено сообщение {msg_id} в чате {chat_id}")
                await asyncio.sleep(1.5)
                break

            except RetryAfter as e:
                wait_time = int(getattr(e, "retry_after", 10))
                print(f"Лимит Telegram: жду {wait_time} сек...")
                await asyncio.sleep(wait_time + 3)

            except Exception as err:
                print(f"Ошибка открепления {msg_id} в {chat_id}: {err}")
                break

async def unpin_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not getattr(msg, "forward_from_chat", None):
        await update.message.reply_text("Перешли то сообщение из источника, которое бот пересылал.")
        return UNPIN_MESSAGE

    original_id = str(msg.forward_from_message_id)
    log_data = load_log()

    target_entries = []

    if original_id in log_data:
        target_entries.extend(log_data[original_id])

    for group_id, entries in log_data.items():
        if group_id.startswith(f"{msg.forward_from_chat.id}_"):
            target_entries.extend(entries)

    if not target_entries:
        await update.message.reply_text("Сообщение не найдено в логе пересылок.")
        return SELECT_ACTION

    unpinned = 0
    failed_chats = []

    chat_entries = {}
    for entry in target_entries:
        chat_id = entry["chat"]
        if chat_id not in chat_entries:
            chat_entries[chat_id] = []
        chat_entries[chat_id].append(entry["msg_id"])


    for chat_id, message_ids in chat_entries.items():
        try:
            target_api = normalize_chat_for_api(chat_id)
            chat = await context.bot.get_chat(target_api)
            member = await context.bot.get_chat_member(chat.id, context.bot.id)

            if member.status != "administrator":
                failed_chats.append(f"{chat_id}: Бот не администратор")
                continue

            if not member.can_pin_messages:
                failed_chats.append(f"{chat_id}: Нет права на открепление")
                continue


            await safe_unpin_messages(context, target_api, message_ids)
            unpinned += 1

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Ошибка открепления в {chat_id}: {error_msg}")
            failed_chats.append(f"{chat_id}: {error_msg}")

    result_message = f"Откреплено {unpinned} сообщений.\n"
    if failed_chats:
        result_message += f"\nНе удалось открепить в {len(failed_chats)} чатах:\n"
        for i, failed in enumerate(failed_chats[:5], 1):
            result_message += f"{i}. {failed}\n"
        if len(failed_chats) > 5:
            result_message += f"... и еще {len(failed_chats) - 5} чатов\n"

    await update.message.reply_text(result_message, reply_markup=main_menu_keyboard())
    return SELECT_ACTION


async def check_bot_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = load_config()
    if not config:
        await update.message.reply_text("Настроек нет. Сначала добавьте источник и цели.")
        return SELECT_ACTION

    permission_results = []

    for source_chat_id, settings in config.items():
        if isinstance(settings, list):
            targets = settings
        else:
            targets = settings.get("targets", [])

        for target in targets:
            try:
                target_api = normalize_chat_for_api(target)
                chat = await context.bot.get_chat(target_api)
                member = await context.bot.get_chat_member(chat.id, context.bot.id)

                permissions = []
                if member.status != "administrator":
                    permissions.append("Не администратор")
                else:
                    if member.can_pin_messages:
                        permissions.append("Может закреплять")
                    else:
                        permissions.append("Не может закреплять")

                    if member.can_delete_messages:
                        permissions.append("Может удалять")
                    else:
                        permissions.append("Не может удалять")

                permission_results.append(f"{target}: {', '.join(permissions)}")

            except Exception as e:
                permission_results.append(f"{target}: Ошибка доступа - {str(e)}")

            await asyncio.sleep(0.5)

    if permission_results:
        message = "**Права бота в целевых чатах:**\n\n" + "\n".join(permission_results)
        if len(message) > 4000:
            parts = [message[i:i + 4000] for i in range(0, len(message), 4000)]
            for part in parts:
                await update.message.reply_text(part, parse_mode="Markdown")
        else:
            await update.message.reply_text(message, parse_mode="Markdown")
    else:
        await update.message.reply_text("Нет целевых чатов для проверки.")

    return SELECT_ACTION


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    asyncio.get_event_loop().create_task(cleanup_old_media_groups())

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu)],
            ADD_SOURCE: [MessageHandler(filters.ALL & ~filters.COMMAND, add_source)],
            ADD_TARGETS: [MessageHandler(filters.ALL & ~filters.COMMAND, add_targets)],
            SET_DELAY: [MessageHandler(filters.ALL & ~filters.COMMAND, set_delay)],
            DELETE_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, delete_forwarded)],
            PIN_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, pin_forwarded)],
            UNPIN_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, unpin_forwarded)],
        },
        fallbacks=[],
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, forward_messages))
    logger.info("Бот-репостер запущен с поддержкой удаления, закрепления и открепления сообщений...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()