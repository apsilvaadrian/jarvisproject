"""Consulta às massivas (quedas de rede) sincronizadas pelo MHnet Notifier.

O MHnet Notifier consulta a planilha "Massivas/Eventos" (via Apps Script) e grava um snapshot em
SQLite em %LOCALAPPDATA%\\MassivaNotifier\\massivas.db. O JARVIS só LÊ esse arquivo (modo somente
leitura), então não interfere no Notifier nem gasta a cota do Apps Script.

Tipos de registro (campo "source"):
- automatic/manual: massiva da planilha (cidade, OLT/slot/PON, data, hora, status, afetados)
- grafana: queda de PPPoE por concentrador (lista de cidades atendidas)
- n2: Avisos N2 (título, mensagem, prioridade)
"""
from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "MassivaNotifier" / "massivas.db"
STALE_MINUTES = 10      # acima disso, avisamos que o Notifier pode estar fechado


def db_path() -> Path:
    return Path(os.environ.get("MHNET_DB") or DEFAULT_DB)


def _norm(text: Any) -> str:
    t = unicodedata.normalize("NFKD", str(text or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def _ativa(item: dict) -> bool:
    return _norm(item.get("status")) not in ("resolvido", "resolvida", "normalizado", "encerrado", "fechado")


def _afetados(item: dict) -> int:
    try:
        return int(float(str(item.get("afetados") or 0).replace(".", "").replace(",", ".")))
    except ValueError:
        return 0


def _locais(item: dict) -> list[str]:
    """Textos onde procurar o lugar: cidade, cidades atendidas, OLT/slot/PON, concentrador."""
    campos = [item.get("cidade"), item.get("olt"), item.get("slot_pon"), item.get("concentrador")]
    campos += list(item.get("cidades") or [])
    return [_norm(c) for c in campos if c]


def _resumo(item: dict) -> dict[str, Any]:
    if item.get("source") == "grafana":
        return {"tipo": "queda PPPoE (Grafana)", "concentrador": item.get("concentrador") or item.get("olt"),
                "cidades": item.get("cidades") or [], "afetados": _afetados(item),
                "queda_percentual": item.get("queda_percentual"), "desde": f"{item.get('data_queda')} {item.get('horario_queda')}",
                "status": item.get("status")}
    return {"tipo": "massiva", "cidade": item.get("cidade"), "olt_slot_pon": item.get("slot_pon") or item.get("olt"),
            "afetados": _afetados(item), "status": item.get("status"),
            "desde": f"{item.get('data_queda')} {item.get('horario_queda')}".strip()}


def _ler() -> tuple[list[dict], datetime | None]:
    path = db_path()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute("SELECT id, payload, last_seen_at FROM massivas").fetchall()
    finally:
        con.close()
    itens, ultima = [], None
    for row_id, payload, last_seen in rows:
        try:
            item = json.loads(payload)
        except json.JSONDecodeError:
            continue
        item.setdefault("_id", str(row_id))
        itens.append(item)
        try:
            t = datetime.fromisoformat(last_seen)
            ultima = t if ultima is None or t > ultima else ultima
        except (TypeError, ValueError):
            pass
    return itens, ultima


def consultar(local: str = "", incluir_resolvidas: bool = False, incluir_avisos: bool = False) -> dict[str, Any]:
    path = db_path()
    if not path.is_file():
        return {"ok": False, "mensagem": "Não encontrei o MHnet Notifier neste computador (massivas.db). "
                                         "Abra o Notifier para ele sincronizar a lista de massivas."}
    try:
        itens, ultima = _ler()
    except sqlite3.Error as exc:
        return {"ok": False, "mensagem": f"Não consegui ler a lista do MHnet Notifier ({exc})."}

    eventos = [i for i in itens if i.get("source") != "n2" and (incluir_resolvidas or _ativa(i))]
    alvo = _norm(local)
    if alvo:
        palavras = [p for p in alvo.split() if len(p) > 2] or [alvo]
        # Casa a frase inteira (ex.: "ponta grossa") ou, se não achar, todas as palavras no mesmo campo
        achados = [i for i in eventos if any(alvo in c for c in _locais(i))]
        if not achados:
            achados = [i for i in eventos if any(all(p in c for p in palavras) for c in _locais(i))]
    else:
        achados = eventos
    achados.sort(key=_afetados, reverse=True)

    resultado: dict[str, Any] = {
        "ok": True,
        "local_pesquisado": local or "(todos)",
        "encontradas": len(achados),
        "massivas": [_resumo(i) for i in achados[:15]],
        "total_ativas_no_geral": sum(1 for i in itens if i.get("source") != "n2" and _ativa(i)),
        "total_afetados_encontrados": sum(_afetados(i) for i in achados),
    }
    if ultima:
        minutos = (datetime.now(timezone.utc) - ultima.astimezone(timezone.utc)).total_seconds() / 60
        resultado["ultima_sincronizacao"] = ultima.astimezone().strftime("%d/%m %H:%M")
        if minutos > STALE_MINUTES:
            resultado["aviso"] = (f"A lista não é atualizada há {int(minutos // 60)} h {int(minutos % 60)} min — "
                                  "o MHnet Notifier pode estar fechado; os dados podem estar desatualizados.")
    if incluir_avisos:
        resultado["avisos_n2"] = [{"titulo": i.get("titulo"), "prioridade": i.get("prioridade"), "status": i.get("status"),
                                   "criado_em": i.get("criado_em")}
                                  for i in itens if i.get("source") == "n2" and _ativa(i)][:10]
    return resultado


# --------------------------------------------------------------------------- mudanças (boas-vindas)
def snapshot() -> dict[str, dict[str, Any]] | None:
    """Massivas ativas agora, por id. None se o Notifier não estiver neste PC."""
    if not db_path().is_file():
        return None
    try:
        itens, _ = _ler()
    except sqlite3.Error:
        return None
    return {str(i["_id"]): _resumo(i) for i in itens if i.get("source") != "n2" and _ativa(i)}


def _onde(r: dict[str, Any]) -> str:
    if r["tipo"].startswith("queda PPPoE"):
        return f"queda de PPPoE no {r.get('concentrador')}"
    return str(r.get("cidade") or r.get("olt_slot_pon") or "local não informado")


def mudancas(antes: dict[str, dict] | None, depois: dict[str, dict] | None) -> str:
    """Frase falada com o que mudou entre dois snapshots ("" se não dá para comparar)."""
    if antes is None or depois is None:
        return ""
    novas = [depois[k] for k in depois if k not in antes]
    resolvidas = [antes[k] for k in antes if k not in depois]
    if not novas and not resolvidas:
        n = len(depois)
        return ("Nenhuma mudança nas massivas enquanto esteve fora: " +
                ("continua sem nenhuma em aberto." if n == 0 else f"continuam {n} em aberto."))
    partes = []
    if novas:
        novas.sort(key=lambda r: r["afetados"], reverse=True)
        lista = ", ".join(f"{_onde(r)} com {r['afetados']} afetados" for r in novas[:3])
        mais = f" e mais {len(novas) - 3}" if len(novas) > 3 else ""
        partes.append(f"{'surgiu 1 massiva nova' if len(novas) == 1 else f'surgiram {len(novas)} massivas novas'}: "
                      f"{lista}{mais}")
    if resolvidas:
        lista = ", ".join(_onde(r) for r in resolvidas[:3])
        partes.append(f"{'1 foi resolvida' if len(resolvidas) == 1 else f'{len(resolvidas)} foram resolvidas'}: {lista}")
    return "Enquanto esteve fora, " + "; e ".join(partes) + f". Agora são {len(depois)} em aberto."


def aviso_sincronizacao() -> str:
    r = consultar()
    return r.get("aviso", "") if r.get("ok") else ""


def para_report() -> dict[str, Any] | None:
    """Números do status report: só as massivas da planilha (a aba do Grafana/PPPoE e os Avisos N2 não contam)."""
    if not db_path().is_file():
        return None
    try:
        itens, _ = _ler()
    except sqlite3.Error:
        return None
    ativas = [i for i in itens if i.get("source") not in ("grafana", "n2") and _ativa(i)]
    return {"eventos": len(ativas), "afetados": sum(_afetados(i) for i in ativas)}
