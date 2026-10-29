"""Apelidos de contatos, avisos silenciados (por conversa e por app), vagas de vários apps e vários monitores."""
from fastapi.testclient import TestClient

from jarvis import avisos, embutidos, flutuante, whatsapp
from jarvis.server import app

client = TestClient(app)


def test_apelido_vira_o_nome_da_conversa():
    assert avisos.apelido("definir", "Bia", "Bia Souza Escritório SP")["ok"]
    assert avisos.nome_para("bia") == "Bia Souza Escritório SP"
    assert avisos.nome_para("Marini") == "Marini"
    assert avisos.apelido_de("Bia Souza Escritório SP") == "Bia"
    assert "Bia = Bia Souza Escritório SP" in avisos.apelido("listar")["mensagem"]
    assert avisos.apelido("remover", "Bia")["ok"] and avisos.nome_para("Bia") == "Bia"


def test_enviar_usa_o_apelido(monkeypatch):
    avisos.apelido("definir", "Bia", "Bia Souza Escritório SP")
    usados = []
    monkeypatch.setattr(whatsapp, "_ponte", lambda: type("P", (), {"conectada": lambda self: True})())
    monkeypatch.setattr(whatsapp, "_enviar_extensao", lambda n, m, p=None: usados.append(n) or {"ok": True, "conversa": n})
    whatsapp.enviar("Bia", "oi")
    assert usados == ["Bia Souza Escritório SP"]
    avisos.apelido("remover", "Bia")


def test_silenciar_e_reativar_conversa_e_app():
    avisos.apelido("definir", "Bia", "Bia Souza Escritório SP")
    assert avisos.silenciar("silenciar", "Bia")["ok"]
    assert avisos.conversa_silenciada("Bia Souza Escritório SP")
    assert not avisos.conversa_silenciada("Marini")
    assert avisos.silenciar("silenciar", "", "instagram")["ok"] and avisos.app_silenciado("instagram")
    assert "Instagram" in avisos.silenciar("listar")["mensagem"]
    assert avisos.silenciar("reativar", "Bia")["ok"] and not avisos.conversa_silenciada("Bia Souza Escritório SP")
    assert avisos.silenciar("reativar", "", "insta")["ok"] and not avisos.app_silenciado("instagram")
    assert not avisos.silenciar("reativar", "Ninguém")["ok"]
    avisos.apelido("remover", "Bia")


def test_parte_do_nome_silencia_o_grupo():
    avisos.silenciar("silenciar", "Digimon")
    assert avisos.conversa_silenciada("DIGIMON - ANARCHY")
    assert not avisos.conversa_silenciada("Digi")
    avisos.silenciar("reativar", "Digimon")


def test_aviso_usa_apelido_e_respeita_silenciados(monkeypatch):
    avisos.apelido("definir", "Bia", "Bia Souza Escritório SP")
    avisos.silenciar("silenciar", "Marini")
    e = embutidos.Embutidos()
    ditos = []
    e.ao_avisar = lambda t, d: ditos.append(t)
    lista = [{"conversa": "Bia Souza Escritório SP", "nao_lidas": 1}, {"conversa": "Marini", "nao_lidas": 1}]
    monkeypatch.setattr(e, "whatsapp", lambda msg, timeout=30: {"ok": True, "conversas": [dict(c) for c in lista]})
    e._avisos_whatsapp()
    lista[0]["nao_lidas"], lista[1]["nao_lidas"] = 2, 5
    e._avisos_whatsapp()
    assert ditos == ["Mensagem nova de Bia."]
    avisos.silenciar("silenciar", "", "whatsapp")              # o app inteiro calado
    lista[0]["nao_lidas"] = 3
    e._avisos_whatsapp()
    assert ditos == ["Mensagem nova de Bia."]
    avisos.silenciar("reativar", "", "whatsapp"); avisos.silenciar("reativar", "Marini"); avisos.apelido("remover", "Bia")


def test_ferramentas_novas():
    from jarvis.tools import TOOLS_BY_NAME
    assert TOOLS_BY_NAME["abrir_app"].handler(app="youtube", onde="card")["onde"] == "card"
    assert TOOLS_BY_NAME["apelido_contato"].handler(acao="listar")["ok"]
    assert TOOLS_BY_NAME["avisos_apps"].handler(acao="listar")["ok"]


def test_varias_vagas_de_apps(monkeypatch):
    e = embutidos.EMBUTIDOS
    monkeypatch.setattr(e, "abrir", lambda app, esperar=25: False)
    r = client.post("/api/apps_web/encaixes", json={"slots": {"whatsapp": {"rect": [0, 0, 300, 400]},
                                                               "insta": {"rect": [310, 0, 300, 400], "clip": [310, 0, 300, 200]},
                                                               "orkut": {"rect": [0, 0, 1, 1]}}, "vista": [1600, 900]})
    assert r.json()["ok"] and set(e.fontes["principal"]["slots"]) == {"whatsapp", "instagram"}
    assert e.fontes["principal"]["slots"]["instagram"]["clip"] == [310.0, 0.0, 300.0, 200.0]
    assert e.ativo == ["instagram", "whatsapp"]
    client.post("/api/apps_web/fechar")
    assert "principal" not in e.fontes


def test_janelinha_nunca_cobre_um_monitor_inteiro(monkeypatch):
    monkeypatch.setattr(flutuante, "_monitores", lambda: [((0, 0, 1920, 1080), (0, 0, 1920, 1032), True),
                                                          ((1920, 0, 3286, 768), (1920, 0, 3286, 768), False)])
    x1, y1, x2, y2 = flutuante._area_total()
    assert (x1, y1) == (0, 0) and x2 == 3285 and y2 == 1032       # o 2º monitor (sem barra) perde 1 px na direita


def test_layout_guarda_os_cards_de_apps():
    from jarvis import cards
    lay = cards.normalizar({"zonas": {"direita": ["app-1730000000000", "musica", "app-x; drop", "<script>"]},
                            "fixados": ["app-1730000000000", "nada"], "ocultos": []})
    assert lay["zonas"]["direita"][:2] == ["app-1730000000000", "musica"]
    assert "app-x; drop" not in sum(lay["zonas"].values(), []) and "<script>" not in sum(lay["zonas"].values(), [])
    assert lay["fixados"] == ["app-1730000000000"]


def test_navegador_como_app(monkeypatch):
    assert embutidos._alias("Google") == "navegador" and embutidos._alias("chrome") == "navegador"
    e = embutidos.Embutidos()
    assert e.ir("navegador", "http://127.0.0.1:8000/api/config")["ok"] is False          # rede local, nunca
    monkeypatch.setattr(embutidos.EMBUTIDOS, "abrir", lambda app, esperar=25: False)
    assert embutidos.navegador_pesquisar("clima")["ok"] is False
    assert embutidos.navegador_pesquisar()["ok"] is False
    from jarvis.tools import PAINEIS, TOOLS_BY_NAME
    assert PAINEIS["navegador_pesquisar"] == "apps" and "navegador_ler" in TOOLS_BY_NAME


def test_fechar_esconde_na_hora(monkeypatch):
    e = embutidos.Embutidos()
    escondidos = []
    monkeypatch.setattr(e, "_esconder_ja", lambda apps: escondidos.extend(apps))
    monkeypatch.setattr(e, "abrir", lambda app, esperar=25: False)
    monkeypatch.setattr(e, "_posicionar", lambda: None)
    e.encaixar_varios("principal", {"whatsapp": {"rect": [0, 0, 10, 10]}, "instagram": {"rect": [0, 0, 10, 10]}}, 1, [100, 100], True)
    e.encaixar_varios("principal", {"whatsapp": {"rect": [0, 0, 10, 10]}}, 1, [100, 100], True)
    assert escondidos == ["instagram"]


def test_tema_dos_apps():
    from jarvis import tema_apps
    s = tema_apps.script(True)
    assert "jarvis-tema" in s and "web.whatsapp.com" in s and "#04121e" in s and ":root { color-scheme" not in s
    assert "const ligado = false" in tema_apps.script(False)
    assert client.post("/api/apps_web/tema", json={"ativo": False}).json() == {"ok": True, "tema": False}
    assert client.get("/api/apps_web").json()["tema"] is False
    client.post("/api/apps_web/tema", json={"ativo": True})


def test_navegador_botoes(monkeypatch):
    e = embutidos.Embutidos()
    assert e.navegar("navegador", "ir", "")["ok"] is False
    idas = []
    monkeypatch.setattr(e, "ir", lambda app, url="": idas.append(url) or {"ok": True})
    e.navegar("navegador", "ir", "motorsport.com/f1")
    e.navegar("navegador", "ir", "gp do méxico horário")
    assert idas == ["https://motorsport.com/f1", "https://www.google.com/search?q=gp+do+m%C3%A9xico+hor%C3%A1rio"]
    assert e.navegar("navegador", "voltar")["ok"] is False                 # navegador fechado
    from jarvis import tema_apps
    assert "mix-blend-mode: screen" in tema_apps.script(True, tinta=True) and "tinta = true" in tema_apps.script(True, tinta=True)
    assert "--WDS-background-wash-plain" in tema_apps.script(True)
