"""Trabalho (StatusRobot, app de massivas), notícias e as falas espontâneas (iniciar/desbloquear), sem rede."""
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from jarvis import boas_vindas, massivas, os_control, server, trabalho, web_tools
from jarvis.brain.local import LocalBrain
from jarvis.tools import ToolExecutor


def _pergunta(texto):
    return LocalBrain().respond([{"role": "user", "text": texto}], ToolExecutor(lambda e: None))


@pytest.fixture
def casa(tmp_path, monkeypatch):
    """Perfil do Windows falso: Área de Trabalho, Documentos e AppData dentro de tmp_path."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    monkeypatch.delenv("JARVIS_STATUSROBOT", raising=False)
    monkeypatch.setattr(trabalho, "_cache", {})
    for d in ("Desktop", "Documents", "AppData/Roaming", "AppData/Local"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    return tmp_path


# --------------------------------------------------------------------------- app de massivas
def test_abre_o_app_de_massivas_pelo_apelido(casa, monkeypatch):
    launcher = casa / "AppData" / "Roaming" / "MHnet Notifier" / "MHnetNotifierLauncher.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("")
    abertos = []
    monkeypatch.setattr(os_control, "IS_WINDOWS", True)
    monkeypatch.setattr(os_control, "_launch_target", abertos.append)
    for nome in ("app de massivas", "MHnet Notifier", "o programa das massivas"):
        r = os_control.open_app(nome)
        assert r["ok"] and "MHnet Notifier" in r["mensagem"]
    assert abertos == [str(launcher)] * 3


def test_jarvis_offline_abre_o_app_em_vez_de_consultar(monkeypatch):
    pedidos = []
    monkeypatch.setattr(os_control, "open_app", lambda nome: pedidos.append(nome) or {"ok": True, "mensagem": "Abrindo."})
    assert _pergunta("abre o app de massivas") == "Abrindo."
    assert pedidos == ["app de massivas"]


def test_programa_portatil_no_appdata_e_encontrado(casa):
    exe = casa / "AppData" / "Local" / "Programs" / "Meu Robo" / "MeuRobo.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (exe.parent / "Uninstall MeuRobo.exe").write_text("")
    assert os_control._user_exe("meu robo") == exe


# --------------------------------------------------------------------------- StatusRobot
def test_localiza_o_statusrobot_nas_pastas_do_usuario(casa):
    exe = casa / "Documents" / "Codex" / "outputs" / "StatusRobotReport.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (casa / "Documents" / "Relatorios MHNET" / "perfil-navegador").mkdir(parents=True)   # não entra aqui
    assert trabalho.localizar() == exe


def test_gera_report_abre_console_e_preenche_o_que_sabe(casa, monkeypatch):
    exe = casa / "Desktop" / "StatusRobotReport.exe"
    exe.write_text("")
    abertos, digitador = [], []

    class Proc:
        pid = 4242

    monkeypatch.setattr(trabalho, "IS_WINDOWS", True)
    monkeypatch.setattr(trabalho, "rodando", lambda: False)
    monkeypatch.setattr(trabalho.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    monkeypatch.setattr(trabalho.subprocess, "Popen", lambda args, **kw: abertos.append(args) or Proc())
    monkeypatch.setattr(trabalho, "_iniciar_preenchimento", lambda pid, regras: digitador.append((pid, dict(regras))))
    monkeypatch.setattr(massivas, "para_report", lambda: {"eventos": 7, "afetados": 823})
    monkeypatch.setattr(massivas, "consultar", lambda *a, **k: {"ok": True})
    r = trabalho.status_robot("gerar", faltas="2", bot_digital="falhou")
    assert r["ok"] and abertos == [[str(exe)]]
    pid, regras = digitador[0]
    assert pid == 4242
    assert regras[trabalho.P_MODO] == "1" and regras[trabalho.P_LOGIN] == ""
    assert regras[trabalho.P_FALTAS] == "2"
    assert regras[trabalho.P_EVENTOS] == "7" and regras[trabalho.P_AFETADOS] == "823"
    assert regras[trabalho.P_BOT_TEL] == "" and regras[trabalho.P_BOT_DIG] == "❌"
    assert "7 eventos e 823 afetados" in r["mensagem"] and r["falta_digitar"] == []

    r = trabalho.status_robot("programar", "18:30")
    assert abertos[-1] == [str(exe), "--scheduled-report"]
    regras = digitador[-1][1]
    assert trabalho.P_MODO not in regras and regras[trabalho.P_HORARIO] == "18:30"
    assert r["falta_digitar"] == ["as faltas do dia"]


def test_digitador_reconhece_cada_pergunta_uma_vez(monkeypatch):
    from jarvis.console_input import proxima_resposta
    monkeypatch.setattr(massivas, "para_report", lambda: {"eventos": 7, "afetados": 823})
    regras, _ = trabalho.respostas(False, faltas="3")
    ja = set()
    assert proxima_resposta("Escolha o modo: ", regras, ja) == (trabalho.P_MODO, "1")
    ja.add(trabalho.P_MODO)
    assert proxima_resposta("Escolha o modo: 1", regras, ja) is None        # já respondida
    assert proxima_resposta("Opcao: ", regras, ja) == (trabalho.P_LOGIN, "")
    assert proxima_resposta("Massivas - Total de Eventos Ativos: ", regras, ja)[0] == trabalho.P_EVENTOS
    assert proxima_resposta("Bot Telefonico [✅/❌, Enter = ✅]: ", regras, ja) == (trabalho.P_BOT_TEL, "")
    assert proxima_resposta("Digite o codigo recebido por e-mail: ", regras, ja) is None   # login fica com você


def test_report_offline_entende_faltas_e_bot(monkeypatch):
    pedidos = []
    monkeypatch.setattr(trabalho, "status_robot", lambda *a: pedidos.append(a) or {"ok": True, "mensagem": "Abri."})
    _pergunta("gera o status report com 2 faltas, o bot digital falhou")
    assert pedidos[-1] == ("gerar", "", "2", "", "falhou")
    _pergunta("programa o report para 18:30 sem faltas")
    assert pedidos[-1][:3] == ("programar", "18:30", "0")


def test_le_o_ultimo_relatorio_salvo(casa):
    pasta = casa / "Documents" / "Relatorios MHNET"
    pasta.mkdir(parents=True)
    (pasta / "relatorio-2026-10-26.txt").write_text("*STATUS REPORT*\nFila: 3", encoding="utf-8")
    (pasta / "relatorio-2026-10-26.json").write_text(json.dumps({"filaDigital": "3", "backlog": "12",
                                                                 "totalEventosAtivos": "9", "faltasDoDia": ""}),
                                                     encoding="utf-8")
    r = trabalho.ultimo_relatorio()
    assert r["ok"] and r["texto"].startswith("STATUS REPORT")       # sem os * do WhatsApp
    assert r["campos"] == {"filaDigital": "3", "backlog": "12", "totalEventosAtivos": "9"}
    assert "Fila digital 3, backlog 12, 9 eventos" in _pergunta("como foi o último status report?")


def test_sem_statusrobot_explica_onde_colocar(casa, monkeypatch):
    monkeypatch.setattr(trabalho, "IS_WINDOWS", True)
    monkeypatch.setattr(trabalho, "rodando", lambda: False)
    r = trabalho.executar()
    assert not r["ok"] and "JARVIS_STATUSROBOT" in r["mensagem"]


# --------------------------------------------------------------------------- notícias
RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Dólar fecha em alta - G1</title><source url="https://g1.globo.com">G1</source>
<pubDate>Mon, 26 Oct 2026 18:00:00 GMT</pubDate></item>
<item><title>Chuva forte no Sul - CNN Brasil</title><source>CNN Brasil</source></item>
</channel></rss>"""


def test_noticias_do_dia_pelo_rss(monkeypatch):
    pedidos = []

    def fake_get(url, params=None, **kw):
        pedidos.append((url, params))
        return httpx.Response(200, text=RSS, request=httpx.Request("GET", url))

    monkeypatch.setattr(web_tools.httpx, "get", fake_get)
    r = web_tools.noticias()
    assert r["ok"] and [n["titulo"] for n in r["noticias"]] == ["Dólar fecha em alta", "Chuva forte no Sul"]
    assert r["noticias"][0]["fonte"] == "G1" and pedidos[0][0].endswith("/rss")
    web_tools.noticias("economia")
    assert pedidos[-1][0].endswith("/search") and pedidos[-1][1]["q"].startswith("economia")
    assert "Dólar fecha em alta, pelo G1" in _pergunta("me fala as notícias")


# --------------------------------------------------------------------------- falas espontâneas
def _r(cidade, afetados):
    return {"tipo": "massiva", "cidade": cidade, "afetados": afetados}


def test_mudancas_nas_massivas_viram_frase():
    antes = {"1": _r("Castro", 10), "2": _r("Irati", 4)}
    assert massivas.mudancas(antes, dict(antes)).startswith("Nenhuma mudança") and "continuam 2" in \
        massivas.mudancas(antes, dict(antes))
    depois = {"2": _r("Irati", 4), "3": _r("Ponta Grossa", 251)}
    frase = massivas.mudancas(antes, depois)
    assert "surgiu 1 massiva nova: Ponta Grossa com 251 afetados" in frase
    assert "1 foi resolvida: Castro" in frase and "Agora são 2 em aberto" in frase
    assert massivas.mudancas(None, depois) == ""          # sem Notifier no PC: só "bem-vindo"


def test_boas_vindas_ao_desbloquear(monkeypatch):
    fotos = iter([{"1": _r("Castro", 10)}, {"1": _r("Castro", 10), "2": _r("Lapa", 30)}])
    monkeypatch.setattr(massivas, "snapshot", lambda: next(fotos))
    monkeypatch.setattr(massivas, "aviso_sincronizacao", lambda: "")
    boas_vindas.ao_bloquear()
    texto = boas_vindas.ao_desbloquear()
    assert texto.startswith("Bem-vindo de volta, Tony.") and "Lapa com 30 afetados" in texto
    assert texto.endswith("Quer os detalhes?")


def test_saudacao_ao_iniciar_pergunta_o_que_fazer(monkeypatch):
    monkeypatch.setitem(boas_vindas.estado, "saudou_inicio", False)
    primeira = boas_vindas.inicio()
    assert "principais notícias" in primeira and "trabalho" in primeira and primeira.endswith("música?")
    assert "notícias" not in boas_vindas.inicio()          # recarregar a página: fala curta


def _ate(ws, tipo):
    for _ in range(50):
        m = ws.receive_json()
        if m["type"] == tipo:
            return m
    raise AssertionError(f"não chegou mensagem {tipo}")


def test_saudacao_pelo_websocket_entra_no_historico(monkeypatch):
    monkeypatch.setitem(boas_vindas.estado, "saudou_inicio", False)
    with TestClient(server.app).websocket_connect("/ws") as ws:
        _ate(ws, "hello")
        ws.send_json({"type": "greet"})
        assert _ate(ws, "reply")["text"].endswith("música?")
        _loop, _send, assistant = server._sessoes[-1]
        assert assistant.history[0]["text"].startswith("[sistema]") and assistant.history[1]["role"] == "assistant"

        # Fala espontânea vinda de outra thread (o vigia do bloqueio de tela)
        threading.Thread(target=server.anunciar, args=("Bem-vindo de volta, Tony.", "desbloqueou")).start()
        assert _ate(ws, "reply")["text"] == "Bem-vindo de volta, Tony."
    assert not server._sessoes                              # janela fechada sai da lista


def test_mostrar_janela_sem_aplicativo_responde_404():
    assert TestClient(server.app).post("/api/mostrar").status_code == 404
