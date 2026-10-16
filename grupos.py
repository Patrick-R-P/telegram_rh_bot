import logging

from telegram.error import ChatMigrated, TelegramError

import database as db

logger = logging.getLogger(__name__)


async def id_atual(bot, chat_id):
    try:
        await bot.get_chat(chat_id)
    except ChatMigrated as e:
        logger.info("Grupo %s virou supergrupo, novo id %s", chat_id, e.new_chat_id)
        db.migrar_grupo(chat_id, e.new_chat_id)
        return e.new_chat_id
    except TelegramError:
        pass
    return chat_id
