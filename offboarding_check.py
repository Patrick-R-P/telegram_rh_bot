"""
Script para rodar uma vez por dia via cron.

Consulta o IXC por colaboradores INATIVOS, identifica quem tinha status
'Ativo' na última verificação (ou seja, foi desligado agora) e remove essas
pessoas de todos os grupos do Telegram conhecidos pelo bot.

Exige que o colaborador já tenha concluído o onboarding (telegram_user_id
preenchido) - sem isso o bot não sabe qual conta pertence a essa pessoa, e o
caso é reportado para tratamento manual em vez de travar a rotina.
"""

import asyncio
import logging
import time

from telegram import Bot
from telegram.error import RetryAfter, TelegramError

import config
import database as db
import ixc_client

logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

PAUSA_ENTRE_CHAMADAS = 0.2  # segundos, para não estourar limites de taxa da API do Telegram


async def remover_de_todos_os_grupos(bot: Bot, telegram_user_id: int):
    sucesso, falhas = 0, 0
    for chat_id in db.listar_todos_grupos():
        try:
            await bot.ban_chat_member(chat_id=chat_id, user_id=telegram_user_id)
            if config.OFFBOARDING_PERMITE_REENTRADA:
                await bot.unban_chat_member(chat_id=chat_id, user_id=telegram_user_id, only_if_banned=True)
            sucesso += 1
        except RetryAfter as e:
            logger.warning("Rate limit do Telegram, aguardando %.1fs", e.retry_after)
            time.sleep(e.retry_after)
        except TelegramError as e:
            # Comum: a pessoa já não era membro desse grupo específico - não é fatal.
            logger.warning("Falha ao remover %s do grupo %s: %s", telegram_user_id, chat_id, e)
            falhas += 1
        time.sleep(PAUSA_ENTRE_CHAMADAS)
    return sucesso, falhas


async def main():
    db.init_db()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    inativos = ixc_client.listar_colaboradores_por_status(config.IXC_VALOR_INATIVO)
    resultados = []

    for bruto in inativos:
        dados = ixc_client.normalizar_colaborador(bruto)
        local = db.get_funcionario(dados["ixc_id"])

        if not local:
            continue  # inativo no IXC, mas nunca passou pelo onboarding do bot

        if local["status_ixc"] != "Ativo" or local["offboarded_em"]:
            continue  # já processado antes (idempotência)

        db.marcar_status(dados["ixc_id"], "Inativo")

        if not local["telegram_user_id"]:
            resultados.append(
                f"⚠️ {dados['nome']}: marcado como Inativo, mas nunca concluiu o onboarding — nada a remover."
            )
            db.marcar_offboarded(dados["ixc_id"])
            continue

        sucesso, falhas = await remover_de_todos_os_grupos(bot, local["telegram_user_id"])
        db.marcar_offboarded(dados["ixc_id"])
        extra = f", falhou em {falhas}" if falhas else ""
        resultados.append(f"✅ {dados['nome']}: removido de {sucesso} grupo(s){extra}.")

    if resultados:
        await bot.send_message(
            chat_id=config.OPS_CHAT_ID,
            text="🔒 Rotina de offboarding executada:\n\n" + "\n".join(resultados),
        )
    else:
        logger.info("Nenhum desligamento novo hoje.")


if __name__ == "__main__":
    asyncio.run(main())
