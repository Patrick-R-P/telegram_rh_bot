"""
Processo principal do bot - fica rodando continuamente via polling.

Fluxo de onboarding (ver README.md para o porquê deste desenho):
  1. /start -> bot pede o nome completo
  2. Bot busca no IXC por colaboradores ATIVOS com esse nome
  3. Se achar candidato(s), pede para o funcionário compartilhar o contato do
     Telegram (botão nativo - devolve um telefone verificado pela plataforma)
  4. Bot confere esse telefone contra o(s) candidato(s); só com uma
     correspondência única ele vincula a conta e envia os convites dos
     grupos (gerais + do setor). Sem correspondência, avisa o RH em vez de
     travar ou arriscar vincular a pessoa errada.
O nome sozinho NUNCA autentica - ele só reduz a lista de candidatos. Quem
autentica é o telefone confirmado pelo Telegram.

Além disso, o bot:
  - Detecta quando é adicionado/promovido a admin num grupo e o registra
  - /classificar <chat_id> geral|setor [nome_setor]: comando administrativo
  - /grupos: lista grupos conhecidos ainda não classificados

offboarding_check.py roda como script separado, via cron, usando o mesmo
token deste bot para remover ex-funcionários dos grupos.
"""

import asyncio
import logging
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

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

AGUARDANDO_NOME, AGUARDANDO_CONTATO = range(2)
MAX_TENTATIVAS_NOME = 5


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ja_cadastrado = db.get_funcionario_by_telegram_id(user.id)
    if ja_cadastrado:
        primeiro_nome = (ja_cadastrado["nome"] or "").split(" ")[0] or "tudo bem"
        await update.message.reply_text(
            f"Oi, {primeiro_nome}! Seu cadastro já está confirmado. "
            "Se precisar de outro convite, fale com o RH."
        )
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
    await update.message.reply_text("Ok, cadastro cancelado. Envie /start para tentar de novo.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def enviar_convites(context: ContextTypes.DEFAULT_TYPE, funcionario: dict):
    destino = funcionario["telegram_user_id"]
    grupos_ids = set(db.listar_grupos_gerais()) | set(db.listar_grupos_setor(funcionario["setor"]))

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

    expira_em = datetime.now(timezone.utc) + timedelta(hours=config.CONVITE_VALIDADE_HORAS)
    linhas = ["Aqui estão os grupos que você deve entrar:"]
    for chat_id in grupos_ids:
        try:
            chat = await context.bot.get_chat(chat_id)
            link = await context.bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=config.CONVITE_LIMITE_USOS,
                expire_date=expira_em,
            )
            linhas.append(f"• {chat.title}: {link.invite_link}")
        except Exception:
            logger.exception("Falha ao gerar convite para o grupo %s", chat_id)
            linhas.append("• (falha ao gerar um dos links — avise o RH/TI)")

    await context.bot.send_message(chat_id=destino, text="\n".join(linhas))


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
            f"\nou:\n/classificar {chat.id} setor NOME_DO_SETOR"
        )
        await context.bot.send_message(chat_id=config.OPS_CHAT_ID, text=aviso)
    elif novo_status in ("left", "kicked"):
        db.remover_grupo(chat.id)


async def classificar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_TELEGRAM_IDS:
        await update.message.reply_text("Você não tem permissão para usar este comando.")
        return

    args = context.args
    if len(args) < 2 or args[1].lower() not in ("geral", "setor"):
        await update.message.reply_text(
            "Uso:\n/classificar <chat_id> geral\n/classificar <chat_id> setor NOME_DO_SETOR"
        )
        return

    chat_id = int(args[0])
    tipo = args[1].lower()
    setor = args[2].strip().lower() if tipo == "setor" and len(args) > 2 else None

    if tipo == "setor" and not setor:
        await update.message.reply_text("Informe o setor: /classificar <chat_id> setor NOME_DO_SETOR")
        return

        if not db.classificar_grupo(chat_id, tipo, setor):
            await update.message.reply_text(
                f"Não achei o grupo {chat_id} cadastrado ainda. Confere com /grupos, "
                "ou remove e readiciona o bot nesse grupo pra ele se cadastrar sozinho."
        )
        return
    extra = f" (setor: {setor})" if setor else ""
    await update.message.reply_text(f"Grupo {chat_id} classificado como '{tipo}'{extra}.")


async def listar_grupos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_TELEGRAM_IDS:
        return
    pendentes = db.listar_grupos_nao_classificados()
    if not pendentes:
        await update.message.reply_text("Todos os grupos conhecidos já estão classificados.")
        return
    linhas = [f"• {g['titulo']} (id {g['chat_id']})" for g in pendentes]
    await update.message.reply_text("Grupos ainda não classificados:\n" + "\n".join(linhas))


def main():
    db.init_db()
    application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AGUARDANDO_NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome)],
            AGUARDANDO_CONTATO: [
                MessageHandler(filters.CONTACT, receber_contato),
                MessageHandler(filters.TEXT & ~filters.COMMAND, contato_invalido),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("classificar", classificar))
    application.add_handler(CommandHandler("grupos", listar_grupos_cmd))
    application.add_handler(ChatMemberHandler(on_bot_membership_change, ChatMemberHandler.MY_CHAT_MEMBER))

    logger.info("Bot iniciado (polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()