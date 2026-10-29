"""Navegador interno: modo leitura, modo página (sem scripts) e o bloqueio de endereços da rede local."""
from fastapi.testclient import TestClient

from jarvis import leitor
from jarvis.server import app

client = TestClient(app)

HTML = """<html><head><title>Título da aba</title>
<meta property="og:title" content="Chuva forte atinge a capital">
<meta property="og:site_name" content="Jornal Teste"><meta property="og:image" content="/capa.jpg">
<script>alert('x')</script><meta http-equiv="refresh" content="0;url=https://outro.com"></head>
<body><nav>Menu Esportes Política</nav>
<article><h1>Chuva forte atinge a capital</h1>
<p>A chuva que caiu nesta terça-feira deixou vários bairros alagados e o trânsito parado por horas.</p>
<img data-src="/fotos/rua.jpg" alt="Rua alagada">
<h2>Previsão</h2><p>A previsão indica mais chuva até o fim de semana, segundo a meteorologia local.</p>
<p><a href="/outra-noticia">Leia também</a></p></article>
<footer>Todos os direitos reservados</footer></body></html>"""


def _sem_rede(monkeypatch):
    monkeypatch.setattr(leitor, "baixar", lambda url: ("https://jornal.teste/noticia", HTML))


def test_leitura_pega_o_texto_principal(monkeypatch):
    _sem_rede(monkeypatch)
    d = leitor.leitura("https://jornal.teste/noticia")
    assert d["ok"] and d["titulo"] == "Chuva forte atinge a capital" and d["site"] == "Jornal Teste"
    assert d["imagem"] == "https://jornal.teste/capa.jpg"
    tipos = [b["tipo"] for b in d["blocos"]]
    assert tipos[:4] == ["p", "img", "h", "p"]
    assert d["blocos"][1]["src"] == "https://jornal.teste/fotos/rua.jpg"
    assert not any("Menu" in b.get("texto", "") or "direitos" in b.get("texto", "") for b in d["blocos"])


def test_pagina_sem_scripts_e_links_ficam_no_jarvis(monkeypatch):
    _sem_rede(monkeypatch)
    html = leitor.pagina("https://jornal.teste/noticia", "http://127.0.0.1:8000/web/pagina?url=")
    assert "<script" not in html and "alert(" not in html and "refresh" not in html
    assert '<base href="https://jornal.teste/noticia"' in html
    assert 'href="http://127.0.0.1:8000/web/pagina?url=https%3A%2F%2Fjornal.teste%2Foutra-noticia"' in html
    assert 'src="/fotos/rua.jpg"' in html                    # imagem "preguiçosa" vira src normal


def test_endpoint_pagina_bloqueia_scripts(monkeypatch):
    _sem_rede(monkeypatch)
    r = client.get("/web/pagina", params={"url": "https://jornal.teste/noticia"})
    assert r.status_code == 200 and "script-src 'none'" in r.headers["content-security-policy"]


def test_nao_abre_rede_local():
    for url in ("http://127.0.0.1:8000/api/config", "http://192.168.0.1/", "http://localhost/", "file:///C:/Windows/win.ini",
                "ftp://exemplo.com/"):
        assert not leitor.seguro(url), url
    d = leitor.leitura("http://127.0.0.1:8000/api/config")
    assert not d["ok"]


def test_links_comuns_nao_passam_pelo_google():
    assert leitor.resolver("https://g1.globo.com/x.ghtml") == "https://g1.globo.com/x.ghtml"


def test_abrir_pagina_vai_para_a_tela():
    from jarvis.tools import PAINEIS, TOOLS
    assert PAINEIS["abrir_pagina"] == "web"
    tool = next(t for t in TOOLS if t.name == "abrir_pagina")
    assert tool.handler(url="https://g1.globo.com")["url"] == "https://g1.globo.com"
