"""Letras de música sincronizadas (LRCLIB, gratuito e sem chave) para o quadro de letra da interface.

O LRCLIB devolve a letra no formato LRC ("[01:23.45] verso"); convertemos para [{t: ms, texto}] e a
interface destaca o verso de acordo com a posição da música no Spotify. A letra vai só para a tela:
o cérebro recebe apenas "mostrando a letra", nunca o texto.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "JARVIS-Projeto-Integrador/1.0 (https://github.com/)"}
_LRC = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
_cache: dict[str, dict[str, Any]] = {}


def parse_lrc(lrc: str) -> list[dict[str, Any]]:
    linhas = []
    for linha in (lrc or "").splitlines():
        tempos = list(_LRC.finditer(linha))
        if not tempos:
            continue
        texto = _LRC.sub("", linha).strip()
        for t in tempos:                       # "[00:10.00][01:20.00] refrão" vale para os dois tempos
            linhas.append({"t": int((int(t.group(1)) * 60 + float(t.group(2))) * 1000), "texto": texto})
    return sorted(linhas, key=lambda x: x["t"])


def _limpa(nome: str) -> str:
    """Tira "- Remastered 2009", "(feat. X)" etc., que atrapalham a busca."""
    nome = re.sub(r"\s+-\s+.*(remaster|version|vers[aã]o|live|ao vivo|mono|stereo|edit).*$", "", nome, flags=re.I)
    return re.sub(r"\s*[\(\[](feat|with|part|remaster)[^\)\]]*[\)\]]", "", nome, flags=re.I).strip()


def _formatar(d: dict[str, Any]) -> dict[str, Any]:
    linhas = parse_lrc(d.get("syncedLyrics") or "")
    if d.get("instrumental"):
        return {"ok": True, "instrumental": True, "sincronizada": False, "linhas": []}
    if linhas:
        return {"ok": True, "sincronizada": True, "linhas": linhas}
    texto = (d.get("plainLyrics") or "").strip()
    if texto:
        return {"ok": True, "sincronizada": False, "linhas": [{"t": None, "texto": t} for t in texto.splitlines()]}
    return {"ok": False}


def buscar(artista: str, musica: str, album: str = "", duracao_ms: int = 0) -> dict[str, Any]:
    artista_principal = (artista or "").split(",")[0].strip()
    chave = f"{artista_principal}|{musica}".lower()
    if chave in _cache:
        return _cache[chave]
    nome = _limpa(musica or "")
    resultado: dict[str, Any] = {"ok": False}
    try:
        params = {"artist_name": artista_principal, "track_name": nome}
        if album:
            params["album_name"] = album
        if duracao_ms:
            params["duration"] = round(duracao_ms / 1000)
        r = httpx.get(f"{API}/get", params=params, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            resultado = _formatar(r.json())
        if not resultado.get("ok") or not resultado.get("sincronizada"):
            # Sem correspondência exata: procura e prefere uma versão sincronizada com duração parecida
            r = httpx.get(f"{API}/search", params={"artist_name": artista_principal, "track_name": nome},
                          headers=HEADERS, timeout=10)
            achados = r.json() if r.status_code == 200 else []
            achados.sort(key=lambda d: (not d.get("syncedLyrics"),
                                        abs((d.get("duration") or 0) - duracao_ms / 1000) if duracao_ms else 0))
            for d in achados[:5]:
                alt = _formatar(d)
                if alt.get("ok") and (alt.get("sincronizada") or not resultado.get("ok")):
                    resultado = alt
                    break
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "mensagem": f"Serviço de letras indisponível ({type(exc).__name__})."}
    if not resultado.get("ok"):
        resultado = {"ok": False, "mensagem": f"Não encontrei a letra de {musica}."}
    resultado.update({"musica": musica, "artista": artista, "fonte": "LRCLIB"})
    if len(_cache) > 60:
        _cache.clear()
    _cache[chave] = resultado
    return resultado


def da_musica_atual() -> dict[str, Any]:
    from . import spotify
    p = spotify.player_state()
    if not p.get("conectado"):
        return {"ok": False, "mensagem": "Conecte o Spotify em ⚙ Configurações para eu acompanhar a letra."}
    if not p.get("ativo"):
        return {"ok": False, "mensagem": "Nada tocando no Spotify agora."}
    return buscar(p.get("artistas", ""), p.get("musica", ""), p.get("album", ""), p.get("duracao_ms", 0))


def mostrar(acao: str = "mostrar") -> dict[str, Any]:
    """Ferramenta do cérebro: abre/fecha o quadro de letra. Não devolve a letra (só para a tela)."""
    if acao == "fechar":
        return {"ok": True, "acao": "fechar", "mensagem": "Quadro de letra fechado."}
    r = da_musica_atual()
    if not r.get("ok"):
        return {"ok": False, "mensagem": r.get("mensagem", "Não encontrei a letra.")}
    tipo = "instrumental" if r.get("instrumental") else "sincronizada" if r.get("sincronizada") else "sem sincronia"
    return {"ok": True, "acao": "mostrar", "musica": r["musica"], "artista": r["artista"], "tipo": tipo,
            "mensagem": f"Mostrando a letra de {r['musica']} na tela ({tipo})."}
