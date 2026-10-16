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
    return listar_bruto(
        controller=config.IXC_EMPLOYEE_CONTROLLER,
        qtype=config.IXC_FIELD_ATIVO,
        query=ativo,
        oper="=",
    )


def normalizar_colaborador(registro: dict) -> dict:
    return {
        "ixc_id": int(registro[config.IXC_FIELD_ID]),
        "nome": registro.get(config.IXC_FIELD_NOME, "") or "",
        "telefone": _somente_digitos(registro.get(config.IXC_FIELD_TELEFONE, "")),
        "setor": (registro.get(config.IXC_FIELD_SETOR) or "").strip().lower(),
        "ativo": registro.get(config.IXC_FIELD_ATIVO, ""),
    }


def buscar_colaborador_por_id(ixc_id: int):
    registros = listar_bruto(
        controller=config.IXC_EMPLOYEE_CONTROLLER,
        qtype=config.IXC_FIELD_ID,
        query=str(ixc_id),
        oper="=",
        rp=1,
    )
    if not registros:
        return None
    return normalizar_colaborador(registros[0])


def buscar_colaboradores_ativos_por_nome(nome_digitado: str) -> list:
    alvo = _normalizar_texto(nome_digitado)
    if not alvo:
        return []

    brutos = listar_bruto(
        controller=config.IXC_EMPLOYEE_CONTROLLER,
        qtype=config.IXC_FIELD_NOME,
        query=nome_digitado,
        oper="L",
    )
    candidatos = [normalizar_colaborador(b) for b in brutos]
    return [
        c for c in candidatos
        if c["ativo"] == config.IXC_VALOR_ATIVO and _normalizar_texto(c["nome"]) == alvo
    ]


def _normalizar_texto(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return texto.strip().lower()


def _somente_digitos(telefone: str) -> str:
    return "".join(ch for ch in (telefone or "") if ch.isdigit())