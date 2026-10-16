import argparse
import asyncio
import logging

from telegram import Bot
from telegram.error import RetryAfter, TelegramError

import config
import database as db
import ixc_client
from grupos import id_atual

logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

PAUSA_ENTRE_CHAMADAS = 0.2
TENTATIVAS = 3


async def com_retry(func, *args, **kwargs):
    for _ in range(TENTATIVAS):
        try:
            return await func(*args, **kwargs)
        except RetryAfter as e:
            logger.warning("Limite de taxa do Telegram, aguardando %ss", e.retry_after)
            await asyncio.sleep(e.retry_after + 1)
    raise RuntimeError("limite de taxa do Telegram")


async def situacao(bot: Bot, chat_id: int, user_id: int):
    membro = await com_retry(bot.get_chat_member, chat_id, user_id)
    if membro.status in ("member", "administrator", "creator"):
        return "dentro"
    if membro.status == "restricted" and getattr(membro, "is_member", True):
        return "dentro"
    return "banido" if membro.status == "kicked" else "fora"


async def processar_usuario(bot: Bot, user_id: int, dry_run: bool):
    grupos = db.listar_grupos()
    presentes, falhas = [], []
    for grupo in grupos:
        chat_id = await id_atual(bot, grupo["chat_id"])
        try:
            estado = await situacao(bot, chat_id, user_id)
            if estado == "dentro":
                if not dry_run:
                    await com_retry(bot.ban_chat_member, chat_id=chat_id, user_id=user_id)
                    if config.OFFBOARDING_PERMITE_REENTRADA:
                        await com_retry(
                            bot.unban_chat_member, chat_id=chat_id, user_id=user_id, only_if_banned=True
                        )
                presentes.append(grupo["titulo"])
            elif estado == "banido" and config.OFFBOARDING_PERMITE_REENTRADA and not dry_run:
                await com_retry(
                    bot.unban_chat_member, chat_id=chat_id, user_id=user_id, only_if_banned=True
                )
        except (TelegramError, RuntimeError) as e:
            logger.warning("Falha no grupo %s para o usuário %s: %s", chat_id, user_id, e)
            falhas.append(f"{grupo['titulo']}: {e}")
        await asyncio.sleep(PAUSA_ENTRE_CHAMADAS)
    return presentes, falhas, len(grupos)


async def main(dry_run: bool, forcar_id):
    db.init_db()
    resultados = []

    async with Bot(token=config.TELEGRAM_BOT_TOKEN) as bot:
        for func in db.listar_vinculados_ativos():
            ixc_id = func["ixc_id"]
            if forcar_id is not None and ixc_id != forcar_id:
                continue

            if forcar_id is None:
                try:
                    atual = await asyncio.to_thread(ixc_client.buscar_colaborador_por_id, ixc_id)
                except Exception:
                    logger.exception("Falha ao consultar o IXC para ixc_id=%s", ixc_id)
                    resultados.append(f"⚠️ {func['nome']}: não consegui consultar o IXC, nada foi alterado.")
                    continue
                if atual is None:
                    resultados.append(f"⚠️ {func['nome']}: cadastro não encontrado no IXC, nada foi alterado.")
                    continue
                if atual["ativo"] != config.IXC_VALOR_INATIVO:
                    continue

            if not dry_run:
                db.marcar_status(ixc_id, "Inativo")
            presentes, falhas, total = await processar_usuario(bot, func["telegram_user_id"], dry_run)
            lista = ", ".join(presentes) or "nenhum"

            if dry_run:
                texto = (
                    f"🔎 {func['nome']}: seria removido de {len(presentes)} grupo(s) ({lista}); "
                    f"o bot conhece {total} grupo(s)."
                )
                if falhas:
                    texto += " Não consegui verificar: " + "; ".join(falhas)
                resultados.append(texto)
            elif falhas:
                resultados.append(
                    f"⚠️ {func['nome']}: removido de {len(presentes)} grupo(s) ({lista}), "
                    f"com falha em {len(falhas)} (nova tentativa na próxima execução): " + "; ".join(falhas)
                )
            else:
                db.marcar_offboarded(ixc_id)
                resultados.append(f"✅ {func['nome']}: removido de {len(presentes)} grupo(s) ({lista}).")

        if not resultados:
            logger.info("Nenhum desligamento novo.")
            return

        texto = "🔒 Rotina de offboarding" + (" (simulação)" if dry_run else "") + ":\n\n" + "\n".join(resultados)
        logger.info("\n%s", texto)
        if not dry_run:
            await bot.send_message(chat_id=config.OPS_CHAT_ID, text=texto)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--forcar-id", type=int)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args.dry_run, args.forcar_id))