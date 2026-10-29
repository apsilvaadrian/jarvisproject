"""Ferramentas de informação da internet, todas gratuitas e sem chave de API:

- buscar_web:        resultados de pesquisa (DuckDuckGo, com reserva na Wikipédia)
- ler_pagina:        texto principal de uma página
- previsao_tempo:    clima atual e próximos dias (Open-Meteo)
- teste_velocidade:  latência, download e upload (servidores da Cloudflare)
"""
from __future__ import annotations

import html
import os
import re
import statistics
import time
import urllib.parse
from typing import Any

import httpx

from .config import settings

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0 Safari/537.36", "Accept-Language": "pt-BR,pt;q=0.9"}


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<.*?>", "", fragment or "")).strip()


# --------------------------------------------------------------------------- pesquisa
def _duckduckgo(consulta: str, limite: int) -> list[dict[str, str]]:
    r = httpx.post("https://html.duckduckgo.com/html/", data={"q": consulta, "kl": "br-pt"}, headers=UA,
                   timeout=12, follow_redirects=True)
    r.raise_for_status()
    blocos = re.split(r'<div class="result results_links', r.text)[1:]
    out = []
    for b in blocos:
        a = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not a or "ad_domain" in b or "result--ad" in b:
            continue
        url = html.unescape(a.group(1))
        if "uddg=" in url:  # link de redirecionamento do DuckDuckGo
            url = urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["uddg"][0])
        s = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S)
        out.append({"titulo": _text(a.group(2)), "url": url, "resumo": _text(s.group(1)) if s else ""})
        if len(out) >= limite:
            break
    return out


def _wikipedia(consulta: str, limite: int) -> list[dict[str, str]]:
    r = httpx.get("https://pt.wikipedia.org/w/api.php", headers=UA, timeout=10, params={
        "action": "query", "list": "search", "srsearch": consulta, "format": "json", "srlimit": limite})
    return [{"titulo": i["title"], "url": "https://pt.wikipedia.org/wiki/" + urllib.parse.quote(i["title"].replace(" ", "_")),
             "resumo": _text(i.get("snippet", ""))} for i in r.json().get("query", {}).get("search", [])]


def buscar_web(consulta: str, limite: int = 5) -> dict[str, Any]:
    limite = max(1, min(int(limite), 8))
    try:
        resultados, fonte = _duckduckgo(consulta, limite), "DuckDuckGo"
    except (httpx.HTTPError, KeyError, IndexError):
        resultados, fonte = [], ""
    if not resultados:
        try:
            resultados, fonte = _wikipedia(consulta, limite), "Wikipédia"
        except httpx.HTTPError as exc:
            return {"ok": False, "mensagem": f"Sem acesso à internet para pesquisar ({exc.__class__.__name__})."}
    if not resultados:
        return {"ok": False, "mensagem": f"Não encontrei resultados para '{consulta}'."}
    return {"ok": True, "consulta": consulta, "fonte": fonte, "resultados": resultados,
            "dica": "Para detalhes, use ler_pagina com a url do resultado mais relevante."}


def ler_pagina(url: str, max_chars: int = 4000) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, headers=UA, timeout=15, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        return {"ok": False, "mensagem": f"Não consegui abrir a página ({exc.__class__.__name__})."}
    if "html" not in r.headers.get("content-type", "html"):
        return {"ok": False, "mensagem": "A página não é um texto que eu consiga ler."}
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav|form|aside)\b.*?</\1>", " ", r.text)
    titulo = _text((re.search(r"(?is)<title>(.*?)</title>", page) or [None, ""])[1])
    main = re.search(r"(?is)<(article|main)\b.*?</\1>", page)
    corpo = main.group(0) if main else page
    corpo = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr)\s*/?>", "\n", corpo)
    texto = re.sub(r"[ \t\r\f\v]+", " ", _text(corpo))
    texto = re.sub(r"\n\s*\n+", "\n", texto).strip()
    return {"ok": True, "url": str(r.url), "titulo": titulo, "texto": texto[:max(500, min(int(max_chars), 8000))]}


# --------------------------------------------------------------------------- clima
_WMO = {0: "céu limpo", 1: "predominantemente limpo", 2: "parcialmente nublado", 3: "nublado", 45: "neblina",
        48: "neblina com geada", 51: "garoa fraca", 53: "garoa", 55: "garoa forte", 61: "chuva fraca", 63: "chuva",
        65: "chuva forte", 66: "chuva congelante", 67: "chuva congelante forte", 71: "neve fraca", 73: "neve",
        75: "neve forte", 80: "pancadas de chuva fracas", 81: "pancadas de chuva", 82: "pancadas de chuva fortes",
        95: "trovoadas", 96: "trovoadas com granizo", 99: "trovoadas com granizo forte"}


GNEWS = "https://news.google.com/rss"
GNEWS_PARAMS = {"hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"}


def noticias(tema: str = "", limite: int = 6) -> dict[str, Any]:
    """Principais notícias do dia (Google Notícias, RSS público) — ou sobre um tema."""
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    limite = max(1, min(int(limite or 6), 12))
    url = f"{GNEWS}/search" if tema else GNEWS
    params = dict(GNEWS_PARAMS, **({"q": f"{tema} when:2d"} if tema else {}))
    try:
        r = httpx.get(url, params=params, headers=UA, timeout=12, follow_redirects=True)
        r.raise_for_status()
        canal = ET.fromstring(r.content).find("channel")
    except (httpx.HTTPError, ET.ParseError) as exc:
        return {"ok": False, "mensagem": f"Não consegui buscar as notícias agora ({type(exc).__name__})."}
    itens = []
    for item in (canal.findall("item") if canal is not None else []):
        titulo = (item.findtext("title") or "").strip()
        fonte = (item.findtext("source") or "").strip()
        if fonte and titulo.endswith(" - " + fonte):
            titulo = titulo[: -len(fonte) - 3]
        try:
            quando = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone().strftime("%d/%m %H:%M")
        except (TypeError, ValueError):
            quando = ""
        if titulo:
            itens.append({"titulo": titulo, "fonte": fonte, "quando": quando, "link": item.findtext("link") or ""})
        if len(itens) >= limite:
            break
    if not itens:
        return {"ok": False, "mensagem": f"Não encontrei notícias{' sobre ' + tema if tema else ''} agora."}
    return {"ok": True, "fonte": "Google Notícias", "tema": tema or "principais do dia", "noticias": itens}


def _local(cidade: str) -> tuple[float, float, str]:
    cidade = (cidade or settings.cidade).strip()
    if cidade:
        r = httpx.get("https://geocoding-api.open-meteo.com/v1/search", timeout=10,
                      params={"name": cidade.split(",")[0], "count": 5, "language": "pt"}).json()
        res = r.get("results") or []
        if not res:
            raise ValueError(f"Não encontrei a cidade '{cidade}'.")
        # Prefere o Brasil quando o nome existe em vários países
        g = next((x for x in res if x.get("country_code") == "BR"), res[0])
        return g["latitude"], g["longitude"], f"{g['name']}, {g.get('admin1', g.get('country', ''))}"
    # Sem cidade configurada: localização aproximada pelo IP
    r = httpx.get("https://ipapi.co/json/", headers=UA, timeout=10).json()
    if "latitude" not in r:
        raise ValueError("Não sei sua cidade. Diga a cidade ou configure JARVIS_CIDADE.")
    return r["latitude"], r["longitude"], f"{r.get('city', '')}, {r.get('region', '')}"


def previsao_tempo(cidade: str = "", dias: int = 3) -> dict[str, Any]:
    try:
        lat, lon, nome = _local(cidade)
        f = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=10, params={
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": max(1, min(int(dias), 7)),
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
        }).json()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"ok": False, "mensagem": str(exc) if isinstance(exc, ValueError) else "Serviço de clima indisponível."}
    c, d = f["current"], f["daily"]
    return {
        "ok": True, "local": nome,
        "agora": {"temperatura_c": c["temperature_2m"], "sensacao_c": c["apparent_temperature"],
                  "umidade_pct": c["relative_humidity_2m"], "vento_kmh": c["wind_speed_10m"],
                  "condicao": _WMO.get(c["weather_code"], "indefinido"), "chuva_mm": c["precipitation"],
                  "codigo": c["weather_code"]},
        "dias": [{"data": d["time"][i], "condicao": _WMO.get(d["weather_code"][i], "indefinido"),
                  "min_c": d["temperature_2m_min"][i], "max_c": d["temperature_2m_max"][i],
                  "chance_chuva_pct": d["precipitation_probability_max"][i], "chuva_mm": d["precipitation_sum"][i],
                  "codigo": d["weather_code"][i]}
                 for i in range(len(d["time"]))],
        "fonte": "Open-Meteo",
    }


# --------------------------------------------------------------------------- velocidade da internet
CF = "https://speed.cloudflare.com"


def teste_velocidade() -> dict[str, Any]:
    """Mede latência (mediana de 5), download (até 25 MB) e upload (até 8 MB). Leva ~10-20 s."""
    try:
        with httpx.Client(headers=UA, timeout=30) as c:
            pings = []
            for i in range(6):
                t = time.perf_counter()
                c.get(f"{CF}/__down", params={"bytes": 0})
                if i:              # a 1ª inclui abrir a conexão (TLS) e distorceria latência e jitter
                    pings.append((time.perf_counter() - t) * 1000)
            latencia = statistics.median(pings)

            def medir_download(n: int) -> tuple[float, float]:
                t = time.perf_counter()
                total = 0
                with c.stream("GET", f"{CF}/__down", params={"bytes": n}) as r:
                    for chunk in r.iter_bytes(65536):
                        total += len(chunk)
                dt = time.perf_counter() - t
                return total * 8 / dt / 1e6, dt

            down, dt = medir_download(10_000_000)
            if dt < 2:                         # conexão rápida: mede de novo com mais dados
                down, _ = medir_download(25_000_000)

            payload = os.urandom(8_000_000)
            t = time.perf_counter()
            c.post(f"{CF}/__up", content=payload)
            up = len(payload) * 8 / (time.perf_counter() - t) / 1e6
    except httpx.HTTPError as exc:
        return {"ok": False, "mensagem": f"Não consegui testar a conexão ({exc.__class__.__name__})."}
    return {"ok": True, "download_mbps": round(down, 1), "upload_mbps": round(up, 1),
            "latencia_ms": round(latencia), "jitter_ms": round(statistics.pstdev(pings)), "servidor": "Cloudflare"}
