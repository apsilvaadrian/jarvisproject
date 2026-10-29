"""Cards da interface: catálogo, layout salvo e o controle por voz/chat ("fixa o card do Spotify").

O layout é uma lista de cards por coluna (esquerda, direita, extra), mais os fixados (ficam no topo da
coluna e continuam visíveis quando o JARVIS mostra cards de contexto) e os ocultos. Ele é salvo em
data/cards.json: tanto o JARVIS (ferramenta controlar_cards) quanto você (arrastando na tela) mexem
no mesmo arquivo, então ele sabe sempre onde está cada card.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from .config import settings
from .logger import log

ZONAS = ("esquerda", "direita", "extra")
# id -> nome na tela, apelidos falados
CATALOGO: dict[str, dict[str, Any]] = {
    "visao": {"nome": "Visão geral", "apelidos": ["visao geral", "visao", "overview", "saude do sistema", "resumo do sistema"]},
    "cpu": {"nome": "Processador", "apelidos": ["processador", "cpu"]},
    "memoria": {"nome": "Memória", "apelidos": ["memoria", "ram"]},
    "gpu": {"nome": "GPU", "apelidos": ["gpu", "placa de video"]},
    "agenda": {"nome": "Agenda", "apelidos": ["agenda", "calendario", "compromissos", "eventos", "google agenda"]},
    "musica": {"nome": "Spotify", "apelidos": ["spotify", "musica", "player", "tocador"]},
    "alertas": {"nome": "Alertas", "apelidos": ["alertas", "avisos", "notificacoes", "alerta"]},
    "conversa": {"nome": "Conversa", "apelidos": ["conversa", "chat", "historico", "log", "mensagens"]},
    "processos": {"nome": "Processos", "apelidos": ["processos", "processo", "tarefas"]},
    "armazenamento": {"nome": "Armazenamento", "apelidos": ["armazenamento", "disco", "discos", "hd", "ssd"]},
    "acoes": {"nome": "Ações rápidas", "apelidos": ["acoes rapidas", "acoes", "atalhos", "botoes"]},
    "cerebros": {"nome": "Cérebros (IA)", "apelidos": ["cerebros", "cerebro", "llm", "ia", "modelos", "provedores"]},
    "servicos": {"nome": "Serviços conectados", "apelidos": ["servicos", "integracoes", "apps conectados", "dispositivos", "conexoes"]},
    "rede": {"nome": "Rede", "apelidos": ["rede", "internet", "conexao"]},
    "sistema": {"nome": "Sistema", "apelidos": ["sistema", "informacoes do sistema"]},
    "email": {"nome": "E-mail", "apelidos": ["email", "e-mail", "gmail", "emails", "caixa de entrada"]},
    "github": {"nome": "GitHub", "apelidos": ["github", "git hub", "repositorios", "pull requests"]},
    "docker": {"nome": "Docker", "apelidos": ["docker", "containers", "conteineres"]},
    "whatsapp": {"nome": "WhatsApp", "apelidos": ["whatsapp", "zap", "wpp", "zapzap"]},
    "instagram": {"nome": "Instagram", "apelidos": ["instagram", "insta"]},
    "facebook": {"nome": "Facebook", "apelidos": ["facebook", "face"]},
}
PADRAO: dict[str, Any] = {
    "zonas": {"esquerda": ["visao", "cpu", "memoria", "gpu", "agenda"],
              "direita": ["musica", "alertas", "conversa", "processos", "armazenamento"],
              "extra": ["acoes", "whatsapp", "instagram", "facebook", "cerebros", "servicos", "email", "rede", "sistema", "github", "docker"]},
    "fixados": [],
    "ocultos": ["github", "docker"],
}
_ZONA_APELIDOS = {
    "esquerda": ["esquerda", "lado esquerdo", "coluna da esquerda", "primeira coluna"],
    "direita": ["direita", "lado direito", "coluna da direita", "meio direito", "segunda coluna"],
    "extra": ["extra", "canto direito", "extrema direita", "ultima coluna", "terceira coluna", "coluna extra"],
}


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).replace("-", " ").split())


def _arquivo():
    return settings.data_dir / "cards.json"


_APP_CARD = re.compile(r"app-\d{1,20}")      # cards de apps/leitura criados na hora (WhatsApp num card etc.)


def _conhecido(cid: Any) -> bool:
    return isinstance(cid, str) and (cid in CATALOGO or bool(_APP_CARD.fullmatch(cid)))


def normalizar(layout: dict[str, Any] | None) -> dict[str, Any]:
    """Layout válido: cada card do catálogo (ou de app) em exatamente uma coluna; fixados/ocultos só com ids conhecidos."""
    layout = layout or {}
    zonas_in = layout.get("zonas") or {}
    vistos: set[str] = set()
    zonas: dict[str, list[str]] = {}
    for z in ZONAS:
        zonas[z] = []
        for cid in zonas_in.get(z) or []:
            if _conhecido(cid) and cid not in vistos:
                zonas[z].append(cid)
                vistos.add(cid)
    for z, ids in PADRAO["zonas"].items():          # cards novos (versão mais nova do JARVIS)
        zonas[z] += [cid for cid in ids if cid not in vistos]
        vistos.update(ids)
    lista = lambda k: [c for c in dict.fromkeys(layout.get(k) or []) if _conhecido(c)]  # noqa: E731
    return {"zonas": zonas, "fixados": lista("fixados"),
            "ocultos": lista("ocultos") if "ocultos" in layout else list(PADRAO["ocultos"])}


def carregar() -> dict[str, Any]:
    try:
        return normalizar(json.loads(_arquivo().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return normalizar(None)


def salvar(layout: dict[str, Any]) -> dict[str, Any]:
    layout = normalizar(layout)
    settings.ensure_dirs()
    _arquivo().write_text(json.dumps(layout, ensure_ascii=False, indent=1), encoding="utf-8")
    return layout


def achar_card(nome: str) -> str | None:
    n = re.sub(r"^(o |a )?(card|cartao|painel|quadro)( d[aeo]s?)?\s+", "", _norm(nome)).strip()
    if n in CATALOGO:
        return n
    for cid, info in CATALOGO.items():                   # apelido exato
        if n in info["apelidos"] or n == _norm(info["nome"]):
            return cid
    for cid, info in CATALOGO.items():                   # apelido dentro da frase ("card do spotify ai")
        if any(re.search(rf"\b{re.escape(a)}\b", n) for a in info["apelidos"]):
            return cid
    return None


def achar_zona(nome: str) -> str | None:
    n = _norm(nome)
    for z in ("extra", "esquerda", "direita"):           # "extrema direita" antes de "direita"
        if n == z or any(a in n for a in _ZONA_APELIDOS[z]):
            return z
    return None


def _onde(layout: dict[str, Any], cid: str) -> str:
    return next(z for z in ZONAS if cid in layout["zonas"][z])


def _posicao(posicao: Any, tamanho: int) -> int:
    p = _norm(str(posicao or ""))
    if p in ("", "fim", "final", "ultimo", "embaixo", "abaixo"):
        return tamanho
    if p in ("topo", "inicio", "primeiro", "cima", "em cima", "acima"):
        return 0
    m = re.search(r"\d+", p)
    return max(0, min(tamanho, int(m.group()) - 1)) if m else tamanho


def _txt(v: Any, limite: int = 120) -> str:
    return " ".join(str(v if v is not None else "").split())[:limite]


def personalizado(titulo: str, tipo: str = "tabela", colunas: list | None = None, linhas: list | None = None,
                  itens: list | None = None, texto: str = "", fonte: str = "", destacar: str = "") -> dict[str, Any]:
    """Card montado pela IA (tabela, lista, números ou texto). Valida e limita o tamanho antes de ir para a tela."""
    tipo = tipo if tipo in ("tabela", "lista", "numeros", "texto") else "tabela"
    card: dict[str, Any] = {"titulo": _txt(titulo, 80) or "Informações", "tipo": tipo, "fonte": _txt(fonte, 80),
                            "destacar": _txt(destacar, 60)}
    if tipo == "tabela":
        cols = [_txt(c, 30) for c in (colunas or [])][:8]
        rows = [[_txt(c, 60) for c in (r if isinstance(r, list) else [r])][:8] for r in (linhas or [])][:40]
        if not rows:
            return {"ok": False, "mensagem": "A tabela veio sem linhas; busque os dados e tente de novo."}
        card.update(colunas=cols, linhas=rows)
    elif tipo in ("lista", "numeros"):
        its = [{"titulo": _txt(i.get("titulo")), "detalhe": _txt(i.get("detalhe"), 160), "valor": _txt(i.get("valor"), 40)}
               for i in (itens or []) if isinstance(i, dict) and i.get("titulo")][:30]
        if not its:
            return {"ok": False, "mensagem": "A lista veio vazia."}
        card["itens"] = its
    else:
        if not _txt(texto):
            return {"ok": False, "mensagem": "O card de texto veio vazio."}
        card["texto"] = _txt(texto, 900)
    log.info("Card personalizado: %s (%s)", card["titulo"], tipo)
    return {"ok": True, "mensagem": f"Card '{card['titulo']}' exibido na tela.", "card": card}


def controlar(acao: str, card: str = "", zona: str = "", posicao: str = "") -> dict[str, Any]:
    """Ferramenta do cérebro. acao: focar | desfocar | fixar | desafixar | mover | ocultar | mostrar | restaurar | listar."""
    acao = _norm(acao)
    layout = carregar()
    if acao == "listar":
        return {"ok": True, "layout": {z: [CATALOGO[c]["nome"] + (" (fixado)" if c in layout["fixados"] else "")
                                           + (" (oculto)" if c in layout["ocultos"] else "")
                                           for c in ids] for z, ids in layout["zonas"].items()}}
    if acao in ("desfocar", "tirar do foco"):
        return {"ok": True, "mensagem": "Card de volta ao lugar.", "foco": "", "layout": layout}
    if acao == "restaurar":
        layout = salvar(PADRAO)
        return {"ok": True, "mensagem": "Cards de volta ao layout padrão.", "layout": layout}
    cid = achar_card(card)
    if cid is None:
        disponiveis = ", ".join(i["nome"] for i in CATALOGO.values())
        return {"ok": False, "mensagem": f"Não conheço o card '{card}'. Cards: {disponiveis}."}
    nome = CATALOGO[cid]["nome"]
    if acao == "fixar":
        layout["fixados"] = [c for c in layout["fixados"] if c != cid] + [cid]
        layout["ocultos"] = [c for c in layout["ocultos"] if c != cid]
        msg = f"Card {nome} fixado no topo da coluna."
    elif acao == "desafixar":
        layout["fixados"] = [c for c in layout["fixados"] if c != cid]
        msg = f"Card {nome} não está mais fixado."
    elif acao == "ocultar":
        layout["ocultos"] = list(dict.fromkeys(layout["ocultos"] + [cid]))
        layout["fixados"] = [c for c in layout["fixados"] if c != cid]
        msg = f"Card {nome} ocultado."
    elif acao == "focar":
        layout["ocultos"] = [c for c in layout["ocultos"] if c != cid]
        layout = salvar(layout)
        return {"ok": True, "mensagem": f"Card {nome} em foco no centro da tela.", "card": cid, "foco": cid, "layout": layout}
    elif acao == "mostrar":
        layout["ocultos"] = [c for c in layout["ocultos"] if c != cid]
        msg = f"Card {nome} visível de novo."
    elif acao == "mover":
        destino = achar_zona(zona) if zona else _onde(layout, cid)
        if destino is None:
            return {"ok": False, "mensagem": f"Não entendi a coluna '{zona}'. Use esquerda, direita ou extra."}
        for z in ZONAS:
            layout["zonas"][z] = [c for c in layout["zonas"][z] if c != cid]
        lista = layout["zonas"][destino]
        lista.insert(_posicao(posicao, len(lista)), cid)
        layout["ocultos"] = [c for c in layout["ocultos"] if c != cid]
        msg = f"Card {nome} movido para a coluna {destino}."
    else:
        return {"ok": False, "mensagem": "Ação inválida: use focar, desfocar, fixar, desafixar, mover, ocultar, mostrar, restaurar ou listar."}
    layout = salvar(layout)
    log.info("Cards: %s %s %s %s", acao, cid, zona, posicao)
    return {"ok": True, "mensagem": msg, "card": cid, "layout": layout}
