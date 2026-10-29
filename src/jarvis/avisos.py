"""Apelidos de contatos e quem/qual app não deve gerar aviso (data/avisos.json).

- Apelido: "chama a Bia Souza Escritório SP de Bia" -> nos comandos "Bia" vira o nome da conversa no WhatsApp,
  e os avisos falam "Mensagem nova de Bia".
- Silenciar: "para de me avisar da Bia / do grupo X / do Instagram" e "volta a me avisar..." — vale só para os
  avisos do JARVIS (nada muda no WhatsApp).
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any

from .config import settings
from .logger import log

APPS = {"whatsapp": "WhatsApp", "instagram": "Instagram", "facebook": "Facebook"}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c) and (c.isalnum() or c.isspace())).split())


def _arquivo():
    return settings.data_dir / "avisos.json"


def carregar() -> dict[str, Any]:
    try:
        d = json.loads(_arquivo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"apelidos": dict(d.get("apelidos") or {}),
            "silenciados": {"conversas": list((d.get("silenciados") or {}).get("conversas") or []),
                            "apps": list((d.get("silenciados") or {}).get("apps") or [])}}


def _salvar(d: dict[str, Any]) -> None:
    settings.ensure_dirs()
    _arquivo().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- apelidos
def nome_para(texto: str) -> str:
    """Apelido -> nome da conversa (se não for apelido, volta o próprio texto)."""
    a = carregar()["apelidos"].get(_norm(texto))
    return a["nome"] if a else texto


def apelido_de(nome: str) -> str:
    """Nome da conversa -> apelido (para os avisos); sem apelido, o próprio nome."""
    n = _norm(nome)
    for a in carregar()["apelidos"].values():
        if _norm(a["nome"]) == n:
            return a["apelido"]
    return nome


def apelido(acao: str, apelido: str = "", nome: str = "") -> dict[str, Any]:
    """Ferramenta: definir | remover | listar."""
    d = carregar()
    acao = _norm(acao)
    if acao == "listar":
        lista = [f"{a['apelido']} = {a['nome']}" for a in d["apelidos"].values()]
        return {"ok": True, "apelidos": lista, "mensagem": "; ".join(lista) if lista else "Nenhum apelido definido."}
    chave = _norm(apelido)
    if not chave:
        return {"ok": False, "mensagem": "Qual é o apelido?"}
    if acao in ("remover", "apagar", "tirar"):
        if d["apelidos"].pop(chave, None) is None:
            return {"ok": False, "mensagem": f"Não tenho o apelido {apelido}."}
        _salvar(d)
        return {"ok": True, "mensagem": f"Apelido {apelido} removido."}
    nome = " ".join((nome or "").split())
    if not nome:
        return {"ok": False, "mensagem": f"De quem é o apelido {apelido}? Diga o nome como aparece no WhatsApp."}
    d["apelidos"][chave] = {"apelido": " ".join(apelido.split()), "nome": nome}
    _salvar(d)
    log.info("Apelido: %s = %s", apelido, nome)
    return {"ok": True, "mensagem": f"Pronto: {apelido} agora é {nome}."}


# --------------------------------------------------------------------------- avisos silenciados
def _app(texto: str) -> str | None:
    n = _norm(texto)
    nomes = {"whatsapp": "whatsapp", "zap": "whatsapp", "wpp": "whatsapp", "instagram": "instagram", "insta": "instagram",
             "facebook": "facebook", "face": "facebook", "fb": "facebook"}
    return nomes.get(n) or nomes.get(n.removeprefix("o ").removeprefix("do "))


def conversa_silenciada(nome: str) -> bool:
    """A conversa (pessoa ou grupo) está na lista dos que não geram aviso? Aceita o nome ou parte dele (>= 4 letras)."""
    n = _norm(nome)
    for s in carregar()["silenciados"]["conversas"]:
        alvo = _norm(nome_para(s))
        if alvo and (alvo == n or (len(alvo) >= 4 and (n.startswith(alvo + " ") or f" {alvo} " in f" {n} "))):
            return True
    return False


def app_silenciado(app: str) -> bool:
    return app in carregar()["silenciados"]["apps"]


def silenciar(acao: str, alvo: str = "", app: str = "") -> dict[str, Any]:
    """Ferramenta: silenciar | reativar | listar. alvo = pessoa/grupo (ou apelido); app = whatsapp/instagram/facebook."""
    d = carregar()
    sil = d["silenciados"]
    acao = _norm(acao)
    if acao == "listar":
        partes = [f"conversas: {', '.join(sil['conversas'])}" if sil["conversas"] else "",
                  f"apps: {', '.join(APPS[a] for a in sil['apps'])}" if sil["apps"] else ""]
        texto = "; ".join(p for p in partes if p)
        return {"ok": True, "silenciados": sil, "mensagem": f"Sem avisos de: {texto}." if texto else "Estou avisando de tudo."}
    reativar = acao in ("reativar", "voltar", "ativar", "volta")
    # Só o app ("para de avisar do Instagram"); com uma pessoa/grupo junto, vale para a conversa
    chave_app = _app(app) if app else (_app(alvo) if alvo else None)
    if chave_app and (not alvo or _app(alvo)):
        if reativar:
            sil["apps"] = [a for a in sil["apps"] if a != chave_app]
            msg = f"Voltei a avisar das novidades do {APPS[chave_app]}."
        else:
            sil["apps"] = list(dict.fromkeys(sil["apps"] + [chave_app]))
            msg = f"Não aviso mais das novidades do {APPS[chave_app]}, até você pedir para voltar."
    else:
        nome = " ".join((alvo or "").split())
        if not nome:
            return {"ok": False, "mensagem": "De quem (ou de qual app) você quer parar ou voltar a receber avisos?"}
        if reativar:
            antes = len(sil["conversas"])
            sil["conversas"] = [c for c in sil["conversas"] if _norm(c) != _norm(nome) and _norm(nome_para(c)) != _norm(nome_para(nome))]
            if len(sil["conversas"]) == antes:
                return {"ok": False, "mensagem": f"{nome} não estava silenciado(a) nos meus avisos."}
            msg = f"Voltei a avisar das mensagens de {nome}."
        else:
            if not any(_norm(c) == _norm(nome) for c in sil["conversas"]):
                sil["conversas"].append(nome)
            msg = f"Não aviso mais das mensagens de {nome}, até você pedir para voltar."
    _salvar(d)
    log.info("Avisos: %s %s %s", acao, alvo, app)
    return {"ok": True, "mensagem": msg}
