"""Cérebros por conta (CLIs oficiais): protocolo de ferramentas por texto, sem rodar as CLIs de verdade."""
import json
import subprocess

import pytest

from jarvis.brain import cli
from jarvis.tools import ToolExecutor


def test_le_chamadas_de_ferramenta_e_variacoes():
    texto = ('<ferramenta>{"nome": "status_sistema", "args": {}}</ferramenta>\n'
             '<função>{"nome": "abrirsite", "args": {"url": "github.com"}}</função>\n'
             '<tool>{"name": "listar_processos", "arguments": {"limite": 3}}</tool>')
    assert cli.parse_tool_calls(texto) == [("status_sistema", {}), ("abrir_site", {"url": "github.com"}),
                                           ("listar_processos", {"limite": 3})]


def test_limpa_markdown_sem_estragar_nomes():
    assert cli.clean_reply("**Pronto**, senhor. O processo `meu_app` foi aberto.") == \
        "Pronto, senhor. O processo meu_app foi aberto."


def test_instrucoes_listam_ferramentas_e_confirmacao():
    texto = cli.instructions()
    assert "<ferramenta>" in texto and "encerrar_processo" in texto
    assert "[pede confirmação do usuário]" in texto


class FakeCli(cli.CliBrain):
    """Simula a CLI: na 1ª chamada pede uma ferramenta, na 2ª responde com o resultado."""
    conta_id = "openai-conta"

    def __init__(self):
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        if "[RESULTADOS" not in prompt:
            return '<ferramenta>{"nome": "data_hora", "args": {}}</ferramenta>'
        return "São **dez** horas, senhor."


def test_executa_ferramenta_e_devolve_resultado_ao_modelo():
    brain = FakeCli()
    acoes = []
    resposta = brain.respond([{"role": "user", "text": "que horas são?"}],
                             ToolExecutor(lambda e: acoes.append(e["tool"]) if e.get("status") == "ok" else None))
    assert resposta == "São dez horas, senhor."
    assert acoes == ["data_hora"]
    assert "data_hora({}) ->" in brain.prompts[1]


def test_acao_perigosa_continua_pedindo_confirmacao():
    class Perigoso(FakeCli):
        def ask(self, prompt):
            if "[RESULTADOS" not in prompt:
                return '<ferramenta>{"nome": "agendar_desligamento", "args": {"acao": "desligar"}}</ferramenta>'
            return "Aguardo sua confirmação, senhor."

    eventos = []
    Perigoso().respond([{"role": "user", "text": "desliga o pc"}], ToolExecutor(eventos.append))
    assert any(e["type"] == "confirm_request" for e in eventos)
    assert not any(e["type"] == "action" for e in eventos)     # nada foi executado sem confirmação


def test_sem_node_no_sistema_usa_o_portatil(monkeypatch, tmp_path):
    from jarvis import contas
    (tmp_path / "node.exe").write_text("")
    (tmp_path / "npm.cmd").write_text("")
    monkeypatch.setattr(contas, "PORTABLE_NODE", tmp_path)
    monkeypatch.setattr(contas, "IS_WINDOWS", True)
    monkeypatch.setattr(contas.shutil, "which", lambda *a: None)          # PC sem Node instalado
    assert contas.node_exe() == str(tmp_path / "node.exe")
    assert contas.npm_cmd() == str(tmp_path / "npm.cmd")
    assert contas.cli_env()["PATH"].startswith(str(tmp_path))
    scripts = []
    monkeypatch.setattr(contas, "_abrir_console", lambda titulo, linhas: scripts.append(linhas))
    contas._abrir_instalacao_npm("gemini-conta")
    texto = "\n".join(scripts[0])
    assert f'set "PATH={tmp_path};%PATH%"' in texto and '--prefix "%APPDATA%\\npm"' in texto


def test_gemini_cli_descontinuado_explica_alternativa():
    erro = cli._login_error("Failed to sign in. Message: This client is no longer supported for Gemini Code Assist "
                            "for individuals. Please migrate to the Antigravity suite", "gemini-conta")
    assert "encerrou" in str(erro) and "chave de API" in str(erro)


def test_inicio_automatico_abre_minimizado():
    from jarvis import desktop
    assert desktop._launch_command().endswith("--minimizado")


def test_erro_de_login_vira_mensagem_clara(monkeypatch):
    brain = cli.ClaudeContaBrain.__new__(cli.ClaudeContaBrain)
    brain.workdir = __import__("pathlib").Path(__import__("tempfile").gettempdir())
    saida = json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"})
    monkeypatch.setattr(brain, "_run", lambda args, stdin: subprocess.CompletedProcess(args, 1, saida, ""))
    with pytest.raises(cli.BrainError, match="não conectada"):
        brain.ask("oi")


def test_exe_corrige_inicio_automatico_que_aponta_para_a_versao_de_dev(monkeypatch, tmp_path):
    from jarvis import desktop
    gravados = []
    monkeypatch.setattr(desktop, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop, "FROZEN", True)
    monkeypatch.setattr(desktop, "set_autostart", lambda on: gravados.append(on) or True)
    monkeypatch.setattr(desktop, "autostart_target", lambda: tmp_path / ".venv" / "pythonw.exe")
    assert desktop.repair_autostart() and gravados == [True]
    exe = tmp_path / "JARVIS.exe"
    exe.write_text("")
    monkeypatch.setattr(desktop, "autostart_target", lambda: exe)          # já aponta para um JARVIS.exe: não mexe
    assert not desktop.repair_autostart() and gravados == [True]
    monkeypatch.setattr(desktop, "autostart_target", lambda: None)         # opção desligada: respeita
    assert not desktop.repair_autostart()
