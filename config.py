"""
Configurações do bot. Todos os valores sensíveis vêm de variáveis de ambiente
- nunca comite tokens no repositório. Use o arquivo .env.example como modelo
e carregue-o com `export $(grep -v '^#' .env | xargs)` ou EnvironmentFile do
systemd (ver README.md).
"""

import os

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_BOT_USERNAME = os.environ["TELEGRAM_BOT_USERNAME"]  # sem o @, ex: SuaEmpresaRHBot

# Grupo/chat interno onde o bot avisa a equipe de RH/TI: novo grupo detectado
# e ainda não classificado, resumo diário do offboarding, erros.
OPS_CHAT_ID = int(os.environ["OPS_CHAT_ID"])

# IDs numéricos do Telegram autorizados a usar comandos administrativos do bot
# (ex: /classificar). Descubra o seu ID conversando com @userinfobot.
ADMIN_TELEGRAM_IDS = {
    int(uid) for uid in os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",") if uid.strip()
}

# --- IXC ---
IXC_BASE_URL = os.environ["IXC_BASE_URL"].rstrip("/")  # ex: https://suaempresa.ixcsoft.com.br/webservice/v1
IXC_USER_ID = os.environ["IXC_USER_ID"]  # ID do usuário de integração (Config. > Usuários > Usuário)
IXC_TOKEN = os.environ["IXC_TOKEN"]      # Token gerado para esse usuário (opção "Permite acesso a API")

# Nome do controller/tabela do IXC onde os colaboradores estão cadastrados.
# "colaborador" é a aposta mais provável (é o termo usado na tela de Ordem de
# Serviço do IXC), mas CONFIRME isso na documentação da sua conta
# (https://wikiapiprovedor.ixcsoft.com.br/) ou com o Suporte API do IXC antes
# de ir para produção - o nome pode variar por versão/customização.
IXC_EMPLOYEE_CONTROLLER = os.environ.get("IXC_EMPLOYEE_CONTROLLER", "colaborador")

# Nomes dos campos dentro desse controller. Ajuste conforme o retorno real da
# sua API - use ixc_client.listar_bruto() manualmente para inspecionar antes
# de confiar nesses nomes (ver README.md, seção "Antes de rodar").
IXC_FIELD_ID = os.environ.get("IXC_FIELD_ID", "id")
IXC_FIELD_NOME = os.environ.get("IXC_FIELD_NOME", "nome")
IXC_FIELD_TELEFONE = os.environ.get("IXC_FIELD_TELEFONE", "telefone_celular")
IXC_FIELD_SETOR = os.environ.get("IXC_FIELD_SETOR", "setor")
IXC_FIELD_ATIVO = os.environ.get("IXC_FIELD_ATIVO", "ativo")
IXC_VALOR_ATIVO = "S"    # convenção comum do IXC para campos booleanos (Sim/Não)
IXC_VALOR_INATIVO = "N"

# --- Comportamento do offboarding ---
# True  -> "kick": remove e permite reentrar (via um novo convite) no futuro -
#          recomendado, já que a pessoa pode ser recontratada.
# False -> banimento permanente: remove e bloqueia qualquer reentrada.
OFFBOARDING_PERMITE_REENTRADA = True

# --- Convites ---
CONVITE_VALIDADE_HORAS = 48
CONVITE_LIMITE_USOS = 1
