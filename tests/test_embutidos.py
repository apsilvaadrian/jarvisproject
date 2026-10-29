"""Apps da web dentro do JARVIS: nomes, encaixe, avisos e o WhatsApp embutido como "ponte"."""
from fastapi.testclient import TestClient

from jarvis import embutidos, whatsapp
from jarvis.server import app

client = TestClient(app)


def test_nomes_dos_apps():
    assert embutidos._alias("WhatsApp") == "whatsapp"
    assert embutidos._alias("zap") == "whatsapp"
    assert embutidos._alias("insta") == "instagram"
    assert embutidos._alias("Face") == "facebook"
    assert embutidos._alias("tiktok") is None


def test_ferramenta_abre_no_painel():
    from jarvis.tools import PAINEIS
    assert PAINEIS["abrir_app"] == "apps"
    assert embutidos.abrir_app("instagram") == {"ok": True, "app": "instagram", "onde": "painel", "mensagem": "Instagram aberto no centro da tela do JARVIS."}
    assert not embutidos.abrir_app("orkut")["ok"]


def test_encaixe_valida_o_app(monkeypatch):
    monkeypatch.setattr(embutidos.EMBUTIDOS, "abrir", lambda app, esperar=25: False)
    r = client.post("/api/apps_web/encaixe", json={"app": "myspace", "rect": [0, 0, 10, 10], "vista": [100, 100]})
    assert r.json()["ok"] is False
    assert client.post("/api/apps_web/encaixe", json={"app": "x", "rect": [1], "vista": [1]}).status_code == 400
    assert client.post("/api/apps_web/fechar").json() == {"ok": True}


def test_aviso_so_quando_o_contador_sobe(monkeypatch):
    e = embutidos.Embutidos()
    avisos = []
    e.ao_avisar = lambda t, d: avisos.append((t, d))
    titulos = {"instagram": "(2) Instagram"}
    monkeypatch.setattr(e, "_hwnd", lambda app: 1 if app in titulos else 0)
    import jarvis.flutuante as fl
    monkeypatch.setattr(fl, "_titulo", lambda h: titulos["instagram"])
    e._conferir_avisos()                                  # primeira leitura: o que já estava lá não vira aviso
    assert avisos == []
    titulos["instagram"] = "(5) Instagram"
    e._conferir_avisos()
    assert avisos == [("3 notificações novas no Instagram.", "")]
    titulos["instagram"] = "Instagram"
    e._conferir_avisos()
    assert len(avisos) == 1


def test_whatsapp_prefere_o_embutido_logado(monkeypatch):
    monkeypatch.setattr(whatsapp.PONTE, "conectada", lambda: True)
    monkeypatch.setattr(whatsapp.EMBUTIDA, "estado", lambda: "pronto")
    assert whatsapp._ponte() is whatsapp.EMBUTIDA
    monkeypatch.setattr(whatsapp.EMBUTIDA, "estado", lambda: None)          # não está aberto: usa a extensão
    assert whatsapp._ponte() is whatsapp.PONTE
    monkeypatch.setattr(whatsapp.EMBUTIDA, "estado", lambda: "login")       # aberto, mas sem login: a extensão ainda serve
    assert whatsapp._ponte() is whatsapp.PONTE
    monkeypatch.setattr(whatsapp.PONTE, "conectada", lambda: False)         # sem extensão: o de dentro explica o que falta
    assert whatsapp._ponte() is whatsapp.EMBUTIDA


def test_whatsapp_embutido_carregando_espera(monkeypatch):
    estados = iter(["carregando", "carregando", "pronto"])
    monkeypatch.setattr(whatsapp.EMBUTIDA, "estado", lambda: next(estados))
    monkeypatch.setattr(whatsapp.time, "sleep", lambda s: None)
    assert whatsapp._ponte() is whatsapp.EMBUTIDA


def test_motivo_do_embutido_fala_do_painel(monkeypatch):
    monkeypatch.setattr(embutidos.EMBUTIDOS, "whatsapp", lambda msg, timeout=30: {"ok": False, "motivo": "login"})
    r = whatsapp.EMBUTIDA.pedir({"type": "nao_lidas"})
    assert r["motivo"] == "embutido_login" and "painel APPS" in whatsapp.MOTIVOS[r["motivo"]]


def test_cards_das_redes(monkeypatch):
    from jarvis import cards
    assert cards.achar_card("card do whatsapp") == "whatsapp" and cards.achar_card("insta") == "instagram"
    monkeypatch.setattr(embutidos.EMBUTIDOS, "_rodando", lambda: False)
    assert client.get("/api/painel/instagram").json() == {"ok": True, "aberto": False, "nome": "Instagram"}


def test_ir_so_dentro_do_proprio_app():
    e = embutidos.Embutidos()
    assert e.ir("instagram", "https://evil.com/instagram.com/")["ok"] is False
    assert e.ir("facebook", "https://www.facebook.com.evil.com/")["ok"] is False
    assert e.ir("instagram", "https://www.instagram.com/direct/inbox/")["mensagem"].startswith("Abra o Instagram")


def test_whatsapp_embutido_sem_janela():
    assert embutidos.Embutidos().whatsapp({"type": "estado"}) == {"ok": False, "motivo": "sem_aba"}


def test_aviso_do_whatsapp_ignora_silenciadas(monkeypatch):
    e = embutidos.Embutidos()
    avisos = []
    e.ao_avisar = lambda t, d: avisos.append((t, d))
    lista = [{"conversa": "Marini", "previa": "oi", "nao_lidas": 1},
             {"conversa": "Grupo Barulhento", "previa": "kkk", "nao_lidas": 50, "silenciada": True}]
    monkeypatch.setattr(e, "whatsapp", lambda msg, timeout=30: {"ok": True, "conversas": [dict(c) for c in lista]})
    e._avisos_whatsapp()                                  # primeira olhada: nada
    assert avisos == []
    lista[1]["nao_lidas"] = 80                            # grupo silenciado: ignora
    e._avisos_whatsapp()
    assert avisos == []
    lista[0]["nao_lidas"] = 2                             # Marini mandou mais uma
    lista.append({"conversa": "Vanessa", "previa": "bom dia", "nao_lidas": 1})
    e._avisos_whatsapp()
    assert avisos == [("Mensagens novas de Marini e Vanessa.", "")]
    lista[0]["nao_lidas"] = 3
    e._avisos_whatsapp()
    assert avisos[-1] == ("Mensagem nova de Marini.", "oi")
    assert e.contagem["whatsapp"] == 4                    # só as não silenciadas contam
