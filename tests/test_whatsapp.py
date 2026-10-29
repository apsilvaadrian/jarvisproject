"""WhatsApp (contatos, confirmação, app x Web) e o cérebro lembrado entre aberturas — sem enviar nada de verdade."""
import pytest
from fastapi.testclient import TestClient

import jarvis.config as cfg
from jarvis import server, whatsapp
from jarvis.tools import ToolExecutor


@pytest.fixture(autouse=True)
def pasta(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)


def test_numeros_brasileiros():
    assert whatsapp.normalizar_numero("(42) 99999-0000") == "5542999990000"
    assert whatsapp.normalizar_numero("+55 41 3333-4444") == "554133334444"
    assert whatsapp.normalizar_numero("123") is None
    assert whatsapp.formatar("5542999990000") == "+55 42 99999-0000"


def test_contatos_salvos_e_numero_direto():
    whatsapp.salvar_contato("João Silva", "41 98888-7777")
    whatsapp.salvar_contato("Maria", "42 99999-0000")
    whatsapp.salvar_contato("Maria Clara", "42 91111-2222")
    assert whatsapp.resolver("joao")[0]["numero"] == "5541988887777"
    assert whatsapp.resolver("Maria")[0]["nome"] == "Maria"                  # nome exato vence
    assert whatsapp.resolver("42 98765-4321")[0]["numero"] == "5542987654321"   # número direto
    assert whatsapp.resolver("Pedro") == (None, "")                          # sem número: procura no WhatsApp
    whatsapp.salvar_contato("Maria", "42 90000-0000")                         # salvar de novo atualiza
    assert [c["numero"] for c in whatsapp.contatos() if c["nome"] == "Maria"] == ["5542900000000"]


@pytest.mark.parametrize("nome, titulos, esperado", [
    ("Marine", ["Marini", "Mariana Lopes"], "Marini"),                        # erro de digitação/voz
    ("familia", ["Família Souza", "Trabalho"], "Família Souza"),                 # sem acento, parte do nome
    ("Maria", ["Maria", "Maria Clara"], "Maria"),                                # exato vence
    ("trabalho equipe", ["Trabalho - Equipe 🚀", "Trabalho"], "Trabalho - Equipe 🚀"),
])
def test_escolhe_a_conversa_certa(nome, titulos, esperado):
    assert whatsapp.escolher(nome, titulos)[0] == esperado


def test_nao_arrisca_quando_ha_duvida():
    escolhida, candidatas = whatsapp.escolher("Maria", ["Maria Clara", "Maria Eduarda"])
    assert escolhida is None and candidatas == ["Maria Clara", "Maria Eduarda"]
    assert whatsapp.escolher("Pedro", ["Marini", "Família"])[0] is None


def test_envio_sempre_pede_confirmacao(monkeypatch):
    enviados = []
    monkeypatch.setattr(whatsapp, "enviar", lambda c, m: enviados.append((c, m)) or {"ok": True, "mensagem": "enviado"})
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")
    ex = ToolExecutor(lambda e: None)
    r = ex.run("whatsapp_enviar", {"contato": "Família Souza", "mensagem": "chego às 8"})
    assert r["status"] == "aguardando_confirmacao" and enviados == []          # nada sai sem o "sim"
    assert "Família Souza" in r["pergunta"] and "procurar no seu WhatsApp" in r["pergunta"]
    ex.resolve_latest(True)
    assert enviados == [("Família Souza", "chego às 8")]
    whatsapp.salvar_contato("Maria", "42 99999-0000")
    assert "(+55 42 99999-0000)" in ex.run("whatsapp_enviar", {"contato": "maria", "mensagem": "oi"})["pergunta"]


def test_modo_app_so_manda_para_numeros(monkeypatch):
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "app")
    r = ToolExecutor(lambda e: None).run("whatsapp_enviar", {"contato": "Família", "mensagem": "oi"})
    assert r["ok"] is False and "só consigo mandar para números" in r["mensagem"]
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")


def test_escolhe_app_ou_web(monkeypatch):
    whatsapp.salvar_contato("Maria", "42 99999-0000")
    usados = []
    monkeypatch.setattr(whatsapp, "_enviar_app", lambda n, m: usados.append(("app", n)) or {"ok": True})
    monkeypatch.setattr(whatsapp, "_enviar_web",
                        lambda m, numero=None, nome=None: usados.append(("web", numero, nome)) or {"ok": True})
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")                 # padrão: Web controlado
    assert whatsapp.enviar("Maria", "oi")["via"] == "WhatsApp Web"
    whatsapp.enviar("Grupo X", "oi")
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "app")
    assert whatsapp.enviar("Maria", "oi")["via"] == "app"
    assert usados == [("web", "5542999990000", None), ("web", None, "Grupo X"), ("app", "5542999990000")]
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")


class _CerebroQueInventa:
    """Simula a IA que diz "abri a confirmação" sem chamar ferramenta nenhuma."""
    name = "teste"

    def respond(self, history, executor):
        return "A janela de confirmação foi aberta na tela."


def _assistente(eventos):
    from jarvis.assistant import Assistant
    a = Assistant(eventos.append)
    a.brain = _CerebroQueInventa()
    return a


def test_pedido_de_whatsapp_nao_depende_da_ia(monkeypatch):
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")
    eventos = []
    a = _assistente(eventos)
    r = a.chat("Jarvis, manda uma mensagem pra Marini dizendo que chego às 8")
    assert "“Marini”" in r and "chego às 8" in r and "Diga sim" in r
    assert any(e["type"] == "confirm_request" for e in eventos)                 # a confirmação existe de verdade
    enviados = []
    monkeypatch.setattr(whatsapp, "enviar", lambda c, m: enviados.append((c, m)) or {"ok": True, "mensagem": "Enviada."})
    assert a.chat("sim") == "Enviada." and enviados == [("Marini", "chego às 8")]


def test_whatsapp_em_duas_etapas_e_cancelar(monkeypatch):
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")
    a = _assistente([])
    assert a.chat("Manda uma mensagem pro grupo da família no WhatsApp.") == "O que devo dizer para família?"
    r = a.chat("diz que vou atrasar")
    assert "“família”" in r and "“vou atrasar”" in r
    a.executor.pending.clear()
    assert a.chat("manda mensagem pro João") == "O que devo dizer para João?"
    assert "não vou enviar" in a.chat("cancela")
    assert "confirmação foi aberta" in a.chat("como está o tempo?")              # conversa normal segue para a IA


def test_contatos_e_modo_pela_tela():
    client = TestClient(server.app)
    r = client.post("/api/whatsapp/contatos", json={"nome": "Ana", "numero": "42 98888-1111"})
    assert r.status_code == 200 and r.json()["contatos"][0]["nome"] == "Ana"
    assert client.post("/api/whatsapp/contatos", json={"nome": "X", "numero": "12"}).status_code == 400
    assert client.post("/api/whatsapp/modo", json={"modo": "app"}).json()["usando"] == "app"
    assert client.post("/api/whatsapp/modo", json={"modo": "web"}).json()["usando"] == "web"
    assert client.post("/api/whatsapp/modo", json={"modo": "pombo"}).status_code == 400
    assert client.delete("/api/whatsapp/contatos/Ana").json()["contatos"] == []
    cfg.settings.whatsapp_modo = "auto"


def test_cerebro_escolhido_fica_salvo(monkeypatch):
    monkeypatch.setattr(cfg.settings, "brain", "local")
    with TestClient(server.app).websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_brain", "brain": "local"})
        ws.send_json({"type": "set_brain", "brain": "claude"})           # sem chave: não salva
        for _ in range(10):
            if ws.receive_json()["type"] == "warning":
                break
    assert cfg.settings.brain == "local"


def test_cerebro_novo_vai_para_o_env(monkeypatch):
    monkeypatch.setattr(cfg.settings, "brain", "local")
    monkeypatch.setattr(cfg.settings, "gemini_key", "AIza-teste")
    with TestClient(server.app).websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "set_brain", "brain": "gemini"})
        for _ in range(10):
            m = ws.receive_json()
            if m["type"] == "hello" and m["brain"] == "gemini":
                break
    assert cfg.settings.brain == "gemini"
    assert "JARVIS_BRAIN=gemini" in cfg.ENV_FILE.read_text(encoding="utf-8")
    assert cfg.settings.default_brain() == "gemini"                      # é com ele que a próxima sessão abre
    monkeypatch.setattr(cfg.settings, "brain", "local")


def test_qual_delas_continua_o_mesmo_envio(monkeypatch):
    monkeypatch.setattr(cfg.settings, "whatsapp_modo", "auto")
    eventos = []
    a = _assistente(eventos)
    chamadas = []

    def enviar(c, m):
        chamadas.append((c, m))
        if c == "Mari":
            return {"ok": False, "motivo": "ambiguo", "candidatas": ["Marini", "Mari Santos"], "texto": m,
                    "mensagem": "Achei mais de uma conversa parecida com Mari: Marini, Mari Santos. Qual delas?"}
        return {"ok": True, "mensagem": f"Mensagem enviada para {c}."}

    monkeypatch.setattr(whatsapp, "enviar", enviar)
    a.chat("manda uma mensagem pra Mari dizendo chego às 8")
    assert "Qual delas?" in a.chat("sim")
    r = a.chat("o nome exato é Marini só isso")                       # não vai para a IA
    assert "“Marini”" in r and "chego às 8" in r and "Diga sim" in r
    assert a.chat("sim") == "Mensagem enviada para Marini."
    assert chamadas == [("Mari", "chego às 8"), ("Marini", "chego às 8")]


@pytest.fixture
def relatorio(tmp_path, monkeypatch):
    from jarvis import trabalho
    pasta = tmp_path / "Relatorios MHNET"
    pasta.mkdir()
    (pasta / "relatorio-2026-10-27.txt").write_text("*STATUS REPORT — Central de Atendimento*\n\nFila: 3", encoding="utf-8")
    monkeypatch.setattr(trabalho, "pasta_relatorios", lambda: pasta)
    monkeypatch.setattr(cfg.settings, "report_grupo", "")
    return pasta


def test_report_vai_inteiro_para_o_grupo_e_o_grupo_fica_lembrado(relatorio, monkeypatch):
    enviados = []
    monkeypatch.setattr(whatsapp, "enviar", lambda c, m: enviados.append((c, m)) or {"ok": True, "contato": "Supervisão MH",
                                                                                   "conferido": True})
    a = _assistente([])
    r = a.chat("Jarvis, manda o status report no grupo Supervisão")
    assert "STATUS REPORT — Central de Atendimento" in r and "“Supervisão”?" in r and "Diga sim" in r
    assert a.chat("sim") == "Status report enviado para Supervisão MH (conferido)."
    assert enviados == [("Supervisão", "*STATUS REPORT — Central de Atendimento*\n\nFila: 3")]   # texto exato
    assert cfg.settings.report_grupo == "Supervisão MH"
    assert "“Supervisão MH”?" in a.chat("manda o report")                    # da próxima vez, sem dizer o grupo
    a.executor.pending.clear()
    monkeypatch.setattr(cfg.settings, "report_grupo", "")


def test_report_sem_grupo_pergunta_qual(relatorio):
    a = _assistente([])
    assert "Para qual grupo" in a.chat("manda o report")
    r = a.chat("grupo dos Supervisores")
    assert "“Supervisores”?" in r
    a.executor.pending.clear()


def test_quando_o_robo_termina_o_jarvis_oferece_o_envio(relatorio, monkeypatch):
    monkeypatch.setattr(cfg.settings, "report_grupo", "Supervisão MH")
    eventos = []
    a = _assistente(eventos)
    fala = a.oferecer_report()
    assert fala.startswith("O status report ficou pronto.") and "Supervisão MH" in fala
    assert any(e["type"] == "confirm_request" for e in eventos)              # o "sim" já envia
    monkeypatch.setattr(cfg.settings, "report_grupo", "")


def test_mensagens_pendentes_sem_abrir_nada(monkeypatch):
    pedidos = []

    def pedir(msg, timeout=30):
        pedidos.append(msg["type"])
        return {"ok": True, "total": 7, "conversas": [
            {"conversa": "Marini", "previa": "oi, tudo bem?", "quando": "03:40", "nao_lidas": 2},
            {"conversa": "Família Souza", "previa": "João: chego amanhã", "quando": "02:10", "nao_lidas": 5}]}

    monkeypatch.setattr(whatsapp.PONTE, "conectada", lambda: True)
    monkeypatch.setattr(whatsapp.PONTE, "pedir", pedir)
    eventos = []
    a = _assistente(eventos)
    r = a.chat("quais minhas mensagens pendentes?")
    assert r.startswith("Você tem 7 mensagem(ns) não lida(s) em 2 conversa(s).") and "Marini (2): oi, tudo bem?" in r
    assert pedidos == ["nao_lidas"]                                           # não abriu conversa nenhuma
    card = next(e for e in eventos if e["type"] == "painel")["dados"]["card"]
    assert card["itens"][1] == {"titulo": "Família Souza", "detalhe": "João: chego amanhã", "valor": "5 · 02:10"}


def test_ler_uma_conversa(monkeypatch):
    respostas = iter([{"ok": True, "titulos": ["Marini", "Mari Santos"]},
                      {"ok": True, "conversa": "Marini", "mensagens": [
                          {"autor": "Você", "quando": "03:30, 27/10/2026", "texto": "oi", "minha": True},
                          {"autor": "Marini", "quando": "03:41, 27/10/2026", "texto": "oi! tudo certo pra hoje?", "minha": False}]}])
    monkeypatch.setattr(whatsapp.PONTE, "conectada", lambda: True)
    monkeypatch.setattr(whatsapp.PONTE, "pedir", lambda msg, timeout=30: next(respostas))
    a = _assistente([])
    r = a.chat("o que a Marine mandou?")
    assert r.startswith("Últimas de Marini") and "Marini: oi! tudo certo pra hoje?" in r


def test_ler_sem_extensao_explica():
    assert "extensão do JARVIS" in whatsapp.pendentes()["mensagem"]


def test_resultados_que_so_citam_o_nome_nao_viram_opcoes():
    # A busca do WhatsApp traz conversas onde "tia Rosana" aparece numa mensagem: nomes nada a ver
    lixo = ["Instagram", "Time de Suporte", "Equipe do Projeto", "Marini", "Financeiro Cobranças"]
    assert whatsapp.escolher("tia Rosana", lixo) == (None, [])
    assert whatsapp.escolher("tia Rosana", [*lixo, "Tia Rosane"])[0] == "Tia Rosane"


def test_busca_de_novo_com_pedaco_do_nome():
    class Ponte:
        def __init__(self):
            self.buscas = []

        def pedir(self, msg, timeout=0):
            self.buscas.append(msg.get("nome"))
            if msg["nome"] == "rosa":
                return {"ok": True, "titulos": ["Tia Rosane", "Rosângela"]}
            return {"ok": True, "titulos": ["Instagram", "Time de Suporte"]}

    p = Ponte()
    _, escolhida, _ = whatsapp._procurar(p, "tia Rosana")
    assert escolhida == "Tia Rosane" and p.buscas == ["tia Rosana", "rosa"]
