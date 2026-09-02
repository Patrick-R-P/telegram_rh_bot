# Bot de RH para Telegram + IXC

Automatiza onboarding e offboarding de funcionários nos grupos corporativos do Telegram, com base no cadastro de colaboradores do IXC.

## Como funciona

**Onboarding** (em tempo real, dentro de `bot.py`):
1. O RH compartilha UM link fixo do bot (`t.me/SeuBotUsername`) com o novo funcionário — por e-mail, WhatsApp, no material de boas-vindas etc. Não precisa de link individual nem esperar nenhum cron rodar.
2. O funcionário clica, dá `/start` e envia o nome completo.
3. O bot busca no IXC por colaboradores **ativos** com esse nome.
4. Como nome não é segredo, o bot pede que a pessoa compartilhe o contato pelo botão nativo do Telegram, e confere esse telefone contra o cadastro do IXC antes de vincular qualquer coisa.
5. Confirmado, o bot vincula a conta e envia os links de convite dos grupos (gerais + do setor).

**Offboarding** (`offboarding_check.py`, cron diário):
1. Consulta o IXC por colaboradores inativos.
2. Compara com o último status conhecido localmente para achar quem acabou de ser desligado.
3. Usa o vínculo criado no onboarding para remover a pessoa (ban+unban = "kick") de todos os grupos conhecidos, como admin.
4. Manda um resumo para o chat operacional.

**Grupos**: em vez de fixar IDs no código, o bot se auto-registra quando é adicionado a um grupo novo e avisa no chat operacional para alguém classificá-lo com `/classificar <chat_id> geral` ou `/classificar <chat_id> setor NOME_DO_SETOR`.

## Arquivos

| Arquivo | Papel |
|---|---|
| `bot.py` | Processo contínuo (polling): onboarding interativo, comandos administrativos |
| `offboarding_check.py` | Cron diário — remove desligados de todos os grupos |
| `onboarding_check.py` | Opcional — cron: lembrete ao RH de quem ainda não confirmou o cadastro |
| `ixc_client.py` | Chamadas ao Webservice do IXC |
| `database.py` | SQLite local (vínculo IXC↔Telegram, grupos classificados) |
| `config.py` | Configuração via variáveis de ambiente |

## Pré-requisitos

1. Python 3.10+
2. Bot criado no [@BotFather](https://t.me/BotFather) (guarde o token)
3. Usuário de integração no IXC com acesso à API liberado (*Configurações > Usuários > Usuário* → opção "Permite acesso a API") e o IP do servidor liberado no grupo de usuários correspondente
4. Um chat/grupo no Telegram para avisos operacionais (`OPS_CHAT_ID`) — pode ser um grupo privado só com RH/TI

## Instalação

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # preencha com os seus dados
export $(grep -v '^#' .env | xargs)   # ou use EnvironmentFile do systemd (ver abaixo)
```

## Antes de rodar: confirme a integração com o IXC

O controller de colaboradores (`IXC_EMPLOYEE_CONTROLLER`, padrão `"colaborador"`) e os nomes de campo (`IXC_FIELD_*`) variam por versão/customização do IXC. Confirme com um teste rápido:

```python
import ixc_client
registros = ixc_client.listar_bruto("colaborador", qtype="ativo", query="S")
print(registros[0])  # inspecione as chaves reais retornadas
```

Ajuste `config.py` (ou as variáveis `IXC_FIELD_*`) até os dados baterem. Se o controller não existir na sua API, a resposta trará algo como "Recurso ... não está disponível" — nesse caso, vale abrir um chamado com o Suporte API da IXC Soft para confirmar o nome certo.

## Rodando

**Processo contínuo** (`bot.py`) — como serviço, por exemplo com systemd:

```ini
# /etc/systemd/system/bot-rh.service
[Unit]
Description=Bot RH Telegram
After=network.target

[Service]
WorkingDirectory=/caminho/para/telegram_rh_bot
EnvironmentFile=/caminho/para/telegram_rh_bot/.env
ExecStart=/caminho/para/telegram_rh_bot/venv/bin/python bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

**Cron** (offboarding é o único obrigatório):

```cron
0 7 * * *  cd /caminho/para/telegram_rh_bot && venv/bin/python offboarding_check.py >> offboarding.log 2>&1
0 9 * * 1  cd /caminho/para/telegram_rh_bot && venv/bin/python onboarding_check.py  >> onboarding.log 2>&1   # opcional, semanal
```

## Primeiro uso

1. Adicione o bot como administrador em cada grupo corporativo (permissões: "convidar via link" e "banir usuários").
2. O bot avisa no `OPS_CHAT_ID` cada grupo novo detectado.
3. Classifique cada um: `/classificar -1001234567890 geral` ou `/classificar -1001234567890 setor comercial`.
4. Teste o `/start` com um cadastro de teste antes de divulgar o link para a empresa toda.

## Pontos de atenção antes de produção

- **Teste em grupos de sandbox primeiro**, principalmente a remoção, antes de apontar para os grupos reais.
- **Homônimos**: se dois colaboradores ativos tiverem o mesmo nome completo, o passo do telefone resolve automaticamente (só um vai bater); se nenhum bater, o RH é avisado para tratar manualmente — o bot nunca "chuta" qual é a pessoa certa.
- **Kick vs. banimento permanente**: por padrão (`OFFBOARDING_PERMITE_REENTRADA = True`) a remoção é reversível — a pessoa pode reentrar com um novo convite se for recontratada. Mude para `False` em `config.py` para bloqueio permanente.
- **Dados pessoais**: `bot_rh.db` guarda telefone, setor e o `user_id` do Telegram de cada colaborador. Trate como dado sensível — permissões de arquivo restritas, backups, e não versione esse arquivo (já está no `.gitignore`).
- **Limites de taxa do Telegram**: `offboarding_check.py` já pausa entre chamadas e trata `RetryAfter`, mas em empresas com centenas de grupos vale acompanhar os logs na primeira execução.
