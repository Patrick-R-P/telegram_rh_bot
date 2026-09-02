"""
Cliente mínimo para o Webservice do IXC.

Padrão usado (confirmado na documentação pública da IXC Soft):
- Base: {IXC_BASE_URL}/{controller}, sempre POST
- Autenticação HTTP Basic usando "ID_DO_USUARIO:TOKEN" como credencial
- Header extra "ixcsoft: listar" indica uma consulta em lista
- Corpo JSON com qtype / query / oper / page / rp / sortname / sortorder
- Operadores aceitos em `oper`: =, !=, >, <, >=, <=, L (contém), NL, IN, NI,
  BE (entre), NBE

IMPORTANTE: o controller de "colaboradores" (config.IXC_EMPLOYEE_CONTROLLER)
e os nomes de campo (config.IXC_FIELD_*) são a parte menos certa desta
integração - variam por versão/customização do IXC de cada provedor.
Confirme-os:
  1. No seu IXC, no manual em Configurações > API, ou na doc oficial
     https://wikiapiprovedor.ixcsoft.com.br/
  2. Chamando listar_bruto() manualmente e inspecionando o JSON retornado
  3. Ou abrindo um chamado com o Suporte API da IXC Soft
Também é comum precisar liberar o IP do servidor que roda este bot no grupo
de usuários vinculado ao usuário de integração (Config. > Usuários > Grupo de
Usuários > Redes permitidas).
"""

import base64
import logging
import unicodedata

import requests

import config

logger = logging.getLogger(__name__)

_AUTH_HEADER = "Basic " + base64.b64encode(
    f"{config.IXC_USER_ID}:{config.IXC_TOKEN}".encode()
).decode()


def _headers(operacao: str) -> dict:
    return {
        "Authorization": _AUTH_HEADER,
        "Content-Type": "application/json",
        "ixcsoft": operacao,
    }


def listar_bruto(controller: str, qtype: str, query: str, oper: str = "=", rp: int = 500) -> list:
    """Consulta genérica 'listar' em qualquer controller do IXC. Também serve
    para explorar/confirmar nomes de campos antes de confiar neles."""
    url = f"{config.IXC_BASE_URL}/{controller}"
    body = {
        "qtype": qtype,
        "query": query,
        "oper": oper,
        "page": "1",
        "rp": str(rp),
        "sortname": qtype,
        "sortorder": "asc",
    }
    resp = requests.post(url, json=body, headers=_headers("listar"), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("registros", [])


def listar_colaboradores_por_status(ativo: str) -> list:
    """Retorna colaboradores brutos com o status informado (config.IXC_VALOR_ATIVO
    ou config.IXC_VALOR_INATIVO)."""
    return listar_bruto(
        controller=config.IXC_EMPLOYEE_CONTROLLER,
        qtype=config.IXC_FIELD_ATIVO,
        query=ativo,
        oper="=",
    )


def normalizar_colaborador(registro: dict) -> dict:
    """Converte um registro bruto do IXC para o formato interno do bot."""
    return {
        "ixc_id": int(registro[config.IXC_FIELD_ID]),
        "nome": registro.get(config.IXC_FIELD_NOME, "") or "",
        "telefone": _somente_digitos(registro.get(config.IXC_FIELD_TELEFONE, "")),
        "setor": (registro.get(config.IXC_FIELD_SETOR) or "").strip().lower(),
        "ativo": registro.get(config.IXC_FIELD_ATIVO, ""),
    }


def buscar_colaboradores_ativos_por_nome(nome_digitado: str) -> list:
    """Busca colaboradores ATIVOS cujo nome bate (ignorando acentos/caixa) com
    o nome informado. Pode retornar mais de um resultado quando há homônimos -
    quem chama deve desempatar por outro fator (ver bot.py: confirmação por
    telefone). Nome sozinho nunca deve ser tratado como autenticação, só como
    forma de localizar o candidato."""
    alvo = _normalizar_texto(nome_digitado)
    if not alvo:
        return []

    brutos = listar_bruto(
        controller=config.IXC_EMPLOYEE_CONTROLLER,
        qtype=config.IXC_FIELD_NOME,
        query=nome_digitado,
        oper="L",  # "contém" - filtro amplo no servidor, refinado abaixo
    )
    candidatos = [normalizar_colaborador(b) for b in brutos]
    return [
        c for c in candidatos
        if c["ativo"] == config.IXC_VALOR_ATIVO and _normalizar_texto(c["nome"]) == alvo
    ]


def _normalizar_texto(texto: str) -> str:
    """Remove acentos e normaliza caixa, para comparar nomes com segurança
    (ex: 'João' e 'joao' devem bater)."""
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return texto.strip().lower()


def _somente_digitos(telefone: str) -> str:
    return "".join(ch for ch in (telefone or "") if ch.isdigit())
