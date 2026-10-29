"""Navegador interno do JARVIS: abre notícias e links dentro da própria interface.

Dois modos:
- leitura: o conteúdo principal da página (título, imagem, texto), no visual do JARVIS;
- página:  a página original servida pelo JARVIS SEM scripts, dentro de um iframe "sandbox", com os links
           reescritos para continuar aqui dentro (a maioria dos sites proíbe ser aberta num iframe direto).
Links do Google Notícias (news.google.com/rss/articles/...) são convertidos no endereço real da notícia.
Só abre endereços da internet: nada de 127.0.0.1, rede local ou outros esquemas além de http/https.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.parse
from typing import Any

import httpx

from .logger import log
from .web_tools import UA

_GNEWS = re.compile(r"^https?://news\.google\.com/(?:rss/)?articles/([^?/#]+)")
_cache_gnews: dict[str, str] = {}


# --------------------------------------------------------------------------- endereço
def seguro(url: str) -> bool:
    """Só http/https para um endereço público (o servidor do JARVIS não pode virar porta para a rede local)."""
    try:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
        return True
    except (ValueError, OSError):
        return False


def resolver(url: str) -> str:
    """Endereço real de um link do Google Notícias (que só redireciona com JavaScript)."""
    m = _GNEWS.match(url or "")
    if not m:
        return url
    art = m.group(1)
    if art in _cache_gnews:
        return _cache_gnews[art]
    try:
        r = httpx.get(f"https://news.google.com/rss/articles/{art}", headers=UA, timeout=10, follow_redirects=True)
        sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
        if not (sg and ts):
            return url
        req = ["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                         f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art}",{ts.group(1)},"{sg.group(1)}"]']
        r2 = httpx.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                        headers={**UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                        content="f.req=" + urllib.parse.quote(json.dumps([[req]])), timeout=10)
        real = json.loads(json.loads(r2.text.split("\n\n")[1])[:-2][0][2])[1]
        if isinstance(real, str) and real.startswith("http"):
            _cache_gnews[art] = real
            return real
    except (httpx.HTTPError, ValueError, IndexError, TypeError, KeyError) as exc:
        log.info("Não consegui converter o link do Google Notícias: %s", exc)
    return url


def baixar(url: str) -> tuple[str, str]:
    """(endereço final, HTML). Confere cada redirecionamento (não pode levar para a rede local)."""
    url = resolver(url.strip())
    with httpx.Client(headers=UA, timeout=15, follow_redirects=False) as c:
        for _ in range(8):
            if not seguro(url):
                raise ValueError("Endereço não permitido.")
            r = c.get(url)
            if r.is_redirect and r.headers.get("location"):
                url = urllib.parse.urljoin(url, r.headers["location"])
                continue
            r.raise_for_status()
            tipo = r.headers.get("content-type", "text/html")
            if "html" not in tipo and "xml" not in tipo:
                raise ValueError("A página não é HTML.")
            return url, r.text
    raise ValueError("Redirecionamentos demais.")


# --------------------------------------------------------------------------- modo leitura
def _meta(soup, *nomes: str) -> str:
    for n in nomes:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if tag and tag.get("content"):
            return " ".join(tag["content"].split())
    return ""


def _img(tag, base: str) -> str:
    src = tag.get("data-src") or tag.get("data-lazy-src") or tag.get("data-original") or tag.get("src") or ""
    if not src and tag.get("srcset"):
        src = tag["srcset"].split(",")[0].split()[0]
    if not src or src.startswith("data:") or re.search(r"(?i)(logo|icon|avatar|sprite|pixel|tracking|beacon|\.svg|/ebc\.(png|gif))", src):
        return ""
    try:
        if int(tag.get("width") or 999) < 120:
            return ""
    except ValueError:
        pass
    return urllib.parse.urljoin(base, src)


def _miolo(soup):
    """O elemento com o texto principal: o <article> com mais texto, ou o "pai" com mais parágrafos longos."""
    artigos = [a for a in soup.find_all("article") if len(a.get_text(" ", strip=True)) > 400]
    if artigos:
        return max(artigos, key=lambda a: sum(len(p.get_text(strip=True)) for p in a.find_all("p")))
    pontos: dict[int, list] = {}
    for p in soup.find_all("p"):
        t = len(p.get_text(strip=True))
        if t < 40:
            continue
        for nivel, pai in enumerate((p.parent, p.parent.parent if p.parent else None)):
            if pai is not None:
                pontos.setdefault(id(pai), [pai, 0.0])[1] += t / (nivel + 1)
    if pontos:
        return max(pontos.values(), key=lambda x: x[1])[0]
    return soup.find("main") or soup.body or soup


def leitura(url: str) -> dict[str, Any]:
    from bs4 import BeautifulSoup
    try:
        final, html = baixar(url)
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "url": url, "mensagem": f"Não consegui abrir a página ({exc})."}
    soup = BeautifulSoup(html, "html.parser")
    titulo = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(" ", strip=True) if soup.title else final)
    site = _meta(soup, "og:site_name") or urllib.parse.urlsplit(final).hostname or ""
    imagem = _meta(soup, "og:image", "twitter:image")
    for lixo in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe", "svg", "button", "template"]):
        lixo.decompose()
    blocos: list[dict[str, str]] = []
    vistos: set[str] = set()
    for el in _miolo(soup).find_all(["h1", "h2", "h3", "p", "li", "blockquote", "img", "figcaption"]):
        if el.name == "img":
            src = _img(el, final)
            if src and src not in vistos and src != imagem:
                vistos.add(src)
                blocos.append({"tipo": "img", "src": src, "alt": el.get("alt", "")[:200]})
            continue
        if el.name == "li" and el.find("p"):
            continue
        if el.find_parent(["blockquote", "li"]) and el.name == "p":
            continue
        texto = " ".join(el.get_text(" ", strip=True).split())
        if not texto or texto in vistos or (el.name in ("li", "figcaption") and len(texto) < 20):
            continue
        if el.name == "h1" and texto == titulo:
            continue
        vistos.add(texto)
        tipo = {"h1": "h", "h2": "h", "h3": "h", "blockquote": "citacao", "li": "item", "figcaption": "legenda"}.get(el.name, "p")
        blocos.append({"tipo": tipo, "texto": texto[:3000]})
        if len(blocos) >= 160:
            break
    texto_total = sum(len(b.get("texto", "")) for b in blocos)
    return {"ok": True, "url": final, "titulo": titulo, "site": site, "imagem": urllib.parse.urljoin(final, imagem) if imagem else "",
            "descricao": _meta(soup, "og:description", "description"),
            "data": _meta(soup, "article:published_time", "og:updated_time")[:16].replace("T", " "),
            "blocos": blocos, "pouco_texto": texto_total < 300}


# --------------------------------------------------------------------------- modo página (sem scripts)
def pagina(url: str, prefixo: str = "/web/pagina?url=") -> str:
    """HTML original, sem scripts, com <base> no site e os links apontando de volta para o navegador interno."""
    from bs4 import BeautifulSoup
    try:
        final, html = baixar(url)
    except (httpx.HTTPError, ValueError) as exc:
        aviso = f"Não consegui abrir {url} ({exc})."
        return f"<!doctype html><meta charset='utf-8'><body style='background:#04101b;color:#cdf6ff;font:16px sans-serif;padding:24px'>{aviso}</body>"
    soup = BeautifulSoup(html, "html.parser")
    for lixo in soup(["script", "iframe", "object", "embed", "base"]):
        lixo.decompose()
    for meta in soup.find_all("meta"):
        if (meta.get("http-equiv") or "").lower() in ("content-security-policy", "refresh"):
            meta.decompose()
    for ns in soup.find_all("noscript"):            # sem JavaScript, o conteúdo de <noscript> é o que vale
        ns.unwrap()
    for img in soup.find_all("img"):                # imagens "preguiçosas" (carregadas por script)
        for attr in ("data-src", "data-lazy-src", "data-original"):
            if img.get(attr):
                img["src"] = img[attr]
                break
        if img.get("data-srcset"):
            img["srcset"] = img["data-srcset"]
        img.attrs.pop("loading", None)
    for a in soup.find_all("a", href=True):
        alvo = urllib.parse.urljoin(final, a["href"])
        if alvo.startswith(("http://", "https://")):
            a["href"] = prefixo + urllib.parse.quote(alvo, safe="")
            a["target"] = "_self"
    cabeca = soup.head or soup
    base = soup.new_tag("base", href=final)
    cabeca.insert(0, base)
    cabeca.insert(0, soup.new_tag("meta", charset="utf-8"))
    return str(soup)
