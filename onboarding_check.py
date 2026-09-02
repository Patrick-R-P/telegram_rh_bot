"""
OPCIONAL - script para rodar via cron (ex: 1x por dia ou por semana).

Com o onboarding acontecendo em tempo real dentro do bot.py (nome + telefone
confirmado), este script deixou de ser necessário para o fluxo principal.
Ele serve só como lembrete para o RH: lista colaboradores ativos no IXC que
ainda não confirmaram o cadastro pelo Telegram, para alguém cutucá-los.

Se preferir, pode simplesmente não colocar este script no cron.
"""

import asyncio
import logging

from telegram import Bot

import config
import database as db
import ixc_client

logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    db.init_db()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)

    ativos = [
        ixc_client.normalizar_colaborador(b)
        for b in ixc_client.listar_colaboradores_por_status(config.IXC_VALOR_ATIVO)
    ]
    pendentes = []

    for dados in ativos:
        local = db.get_funcionario(dados["ixc_id"])
        if local and local["telegram_user_id"]:
            continue  # já onboardado
        pendentes.append(dados["nome"])

    if pendentes:
        linhas = ["📋 Colaboradores ativos que ainda não confirmaram o cadastro no bot:"]
        linhas += [f"• {nome}" for nome in pendentes]
        linhas.append(f"\nLink do bot: https://t.me/{config.TELEGRAM_BOT_USERNAME}")
        await bot.send_message(chat_id=config.OPS_CHAT_ID, text="\n".join(linhas))
    else:
        logger.info("Todos os colaboradores ativos já confirmaram o cadastro.")


if __name__ == "__main__":
    asyncio.run(main())
