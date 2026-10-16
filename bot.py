import asyncio
import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import config
import database as db
import ixc_client
from grupos import id_atual

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

AGUARDANDO_NOME, AGUARDANDO_CONTATO = range(2)
MAX_TENTATIVAS_NOME = 5


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ja_cadastrado = db.get_funcionario_by_telegram_id(user.id)
    if ja_cadastrado:
        if ja_cadastrado["offboarded_em"]:
            try:
                atual = await asyncio.to_thread(
                    ixc_client.buscar_colaborador_por_id, ja_cadastrado["ixc_id"]
                )
            except Exception:
                logger.exception("Falha ao consultar o IXC para ixc_id=%s", ja_cadastrado["ixc_id"])
                await update.message.reply_text(
                    "Não consegui consultar seu cadastro agora. Tente de novo em instantes."
                )
                return ConversationHandler.END
            if not atual or atual["ativo"] != config.IXC_VALOR_ATIVO:
                await update.message.reply_text("Seu cadastro não está mais ativo. Fale com o RH.")
                return ConversationHandler.END
            db.reativar_funcionario(ja_cadastrado["ixc_id"])
            ja_cadastrado = db.get_funcionario(ja_cadastrado["ixc_id"])
        primeiro_nome = (ja_cadastrado["nome"] or "").split(" ")[0] or "tudo bem"
        await update.message.reply_text(f"Oi, {primeiro_nome}! Vou te mandar os convites de novo.")
        await enviar_convites(context, ja_cadastrado)
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "Olá! Vou te ajudar a entrar nos grupos da empresa. "
        "Envie seu nome completo, exatamente como está no seu cadastro."
    )
    return AGUARDANDO_NOME


async def receber_nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tentativas = context.user_data.get("tentativas_nome", 0) + 1
    context.user_data["tentativas_nome"] = tentativas

    nome_digitado = update.message.text.strip()
    candidatos = await asyncio.to_thread(ixc_client.buscar_colaboradores_ativos_por_nome, nome_digitado)

    if not candidatos:
        if tentativas >= MAX_TENTATIVAS_NOME:
            await update.message.reply_text("Não consegui localizar seu cadastro. Fale diretamente com o RH.")
            return ConversationHandler.END
        await update.message.reply_text(
            "Não encontrei esse nome entre os colaboradores ativos. "
            "Confira a grafia (nome completo) e tente de novo."
        )
        return AGUARDANDO_NOME

    context.user_data["candidatos"] = candidatos
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Compartilhar meu contato", request_contact=True)]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "Encontrei seu cadastro. Para confirmar que é você, compartilhe seu contato abaixo "
        "(é o número que temos no seu cadastro):",
        reply_markup=keyboard,
    )
    return AGUARDANDO_CONTATO


async def contato_invalido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Use o botão "Compartilhar meu contato" para confirmar seu cadastro.')
    return AGUARDANDO_CONTATO


async def receber_contato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contato = update.message.contact
    if contato.user_id != update.effective_user.id:
        await update.message.reply_text("Preciso do SEU contato, não do de outra pessoa. Tente de novo.")
        return AGUARDANDO_CONTATO

    candidatos = context.user_data.get("candidatos", [])
    telefone_compartilhado = "".join(ch for ch in contato.phone_number if ch.isdigit())
    correspondentes = [
        c for c in candidatos
        if c["telefone"] and telefone_compartilhado.endswith(c["telefone"][-8:])
    ]

    if len(correspondentes) != 1:
        await update.message.reply_text(
            "Não consegui confirmar automaticamente com esse número. "
            "O RH foi avisado e vai te ajudar por aqui.",
            reply_markup=ReplyKeyboardRemove(),
        )
        await context.bot.send_message(
            chat_id=config.OPS_CHAT_ID,
            text=(
                "⚠️ Onboarding manual necessário: "
                f"@{update.effective_user.username or '(sem username)'} "
                f"({update.effective_user.full_name}, id {update.effective_user.id}) "
                "não confirmou automaticamente por telefone."
            ),
        )
        context.user_data.clear()
        return ConversationHandler.END

    dados = correspondentes[0]
    db.upsert_funcionario_ixc(dados["ixc_id"], dados["nome"], dados["telefone"], dados["setor"])
    db.vincular_telegram(dados["ixc_id"], update.effective_user.id)

    await update.message.reply_text("Confirmado! ✅", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()

    funcionario = db.get_funcionario(dados["ixc_id"])
    await enviar_convites(context, funcionario)
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Ok, cadastro cancelado. Envie /start para tentar de novo.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def enviar_convites(context: ContextTypes.DEFAULT_TYPE, funcionario: dict):
    destino = funcionario["telegram_user_id"]
    gerais = db.listar_grupos_gerais()
    do_setor = db.listar_grupos_setor(funcionario["setor"])
    logger.info(
        "Convites ixc_id=%s setor=%r grupos_gerais=%s grupos_do_setor=%s db=%s",
        funcionario["ixc_id"], funcionario["setor"], gerais, do_setor, db.DB_PATH,
    )
    grupos_ids = list(dict.fromkeys(gerais + do_setor))

    if not grupos_ids:
        await context.bot.send_message(
            chat_id=destino,
            text=(
                "Seu cadastro foi confirmado, mas ainda não há grupos "
                f"configurados para o setor '{funcionario['setor']}'. O RH foi avisado."
            ),
        )
        await context.bot.send_message(
            chat_id=config.OPS_CHAT_ID,
            text=(
                f"⚠️ {funcionario['nome']} (setor: {funcionario['setor']}) concluiu o onboarding, "
                "mas não há grupos 'geral' nem desse setor classificados ainda."
            ),
        )
        return

    grupos_ids = list(dict.fromkeys([await id_atual(context.bot, c) for c in grupos_ids]))

    pendentes = []
    for chat_id in grupos_ids:
        try:
            membro = await context.bot.get_chat_member(chat_id, destino)
            if membro.status in ("member", "administrator", "creator"):
                continue
        except Exception:
            logger.warning("Não consegui checar se %s já está no grupo %s", destino, chat_id)
        pendentes.append(chat_id)

    if not pendentes:
        await context.bot.send_message(
            chat_id=destino,
            text="Você já está em todos os grupos do seu setor. Se faltar algum, fale com o RH.",
        )
        return

    await context.bot.send_message(
        chat_id=destino,
        text=f"Esses são os seus grupos. Os links expiram em {config.CONVITE_VALIDADE_HORAS}h:",
    )
    expira_em = datetime.now(timezone.utc) + timedelta(hours=config.CONVITE_VALIDADE_HORAS)
    for chat_id in pendentes:
        try:
            chat = await context.bot.get_chat(chat_id)
            link = await context.bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=config.CONVITE_LIMITE_USOS,
                expire_date=expira_em,
            )
            logger.info(
                "Link gerado grupo=%s limite=%s expira=%s",
                chat_id, link.member_limit, link.expire_date,
            )
            await context.bot.send_message(chat_id=destino, text=f"{chat.title}\n{link.invite_link}")
        except Exception:
            logger.exception("Falha ao gerar convite para o grupo %s", chat_id)
            await context.bot.send_message(
                chat_id=destino,
                text="Não consegui gerar o link de um dos grupos. Avise o RH/TI.",
            )


async def on_bot_membership_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    chat = result.chat
    if chat.type not in ("group", "supergroup"):
        return

    novo_status = result.new_chat_member.status
    if novo_status in ("administrator", "member"):
        db.upsert_grupo(chat_id=chat.id, titulo=chat.title)
        aviso = f"Novo grupo detectado: '{chat.title}' (id {chat.id})."
        if novo_status == "member":
            aviso += "\n⚠️ O bot ainda não é administrador aqui - promova-o para liberar convites e remoções."
        aviso += (
            f"\nClassifique com:\n/classificar {chat.id} geral"
            f"\nou:\n/classificar {chat.id} setor NUMERO_DO_SETOR"
        )
        await context.bot.send_message(chat_id=config.OPS_CHAT_ID, text=aviso)
    elif novo_status in ("left", "kicked"):
        db.remover_grupo(chat.id)


async def classificar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_TELEGRAM_IDS:
        await update.message.reply_text("Você não tem permissão para usar este comando.")
        return

    args = context.args
    tipo = args[1].lower() if len(args) > 1 else ""
    valido = (
        args[0].lstrip("-").isdigit()
        and (
            (tipo == "geral" and len(args) == 2)
            or (tipo == "setor" and len(args) == 3 and args[2].isdigit())
        )
    ) if args else False
    if not valido:
        await update.message.reply_text(
            "Uso:\n/classificar <chat_id> geral\n"
            "/classificar <chat_id> setor <número do setor no IXC>"
        )
        return

    chat_id = int(args[0])
    setor = args[2] if tipo == "setor" else None

    if not db.classificar_grupo(chat_id, tipo, setor):
        await update.message.reply_text(
            f"Não achei o grupo {chat_id} cadastrado ainda. Confere com /status, "
            "ou remove e readiciona o bot nesse grupo pra ele se cadastrar sozinho."
        )
        return

    grupo = db.get_grupo(chat_id)
    await update.message.reply_text(
        f"Gravado no banco: {grupo['chat_id']} ({grupo['titulo']}) tipo={grupo['tipo']} setor={grupo['setor']}."
    )


async def listar_grupos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    pendentes = db.listar_grupos_nao_classificados()
    if not pendentes:
        await update.message.reply_text("Todos os grupos conhecidos já estão classificados.")
        return
    linhas = [f"• {g['titulo']} (id {g['chat_id']})" for g in pendentes]
    await update.message.reply_text("Grupos ainda não classificados:\n" + "\n".join(linhas))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    linhas = [f"host: {socket.gethostname()}", f"pid: {os.getpid()}", f"db: {db.DB_PATH}"]
    for g in db.listar_grupos():
        linhas.append(f"{g['chat_id']} | {g['titulo']} | {g['tipo']} | {g['setor']}")
    await update.message.reply_text("\n".join(linhas))


def main():
    db.init_db()
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    novas = filters.UpdateType.MESSAGE

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start, filters=novas)],
        states={
            AGUARDANDO_NOME: [MessageHandler(novas & filters.TEXT & ~filters.COMMAND, receber_nome)],
            AGUARDANDO_CONTATO: [
                MessageHandler(novas & filters.CONTACT, receber_contato),
                MessageHandler(novas & filters.TEXT & ~filters.COMMAND, contato_invalido),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar, filters=novas)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("classificar", classificar, filters=novas))
    application.add_handler(CommandHandler("grupos", listar_grupos_cmd, filters=novas))
    application.add_handler(CommandHandler("status", status, filters=novas))
    application.add_handler(ChatMemberHandler(on_bot_membership_change, ChatMemberHandler.MY_CHAT_MEMBER))

    logger.info("Bot iniciado (polling) pid=%s db=%s", os.getpid(), db.DB_PATH)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()