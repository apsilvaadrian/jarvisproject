"""Ferramentas, confirmação de ações sensíveis e manipulação de arquivos/processos."""
import os
import subprocess
import sys
from pathlib import Path

import psutil

from jarvis.assistant import Assistant
from jarvis.config import settings
from jarvis.tools import TOOLS, ToolExecutor


def _spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def test_schemas_validos():
    nomes = set()
    for t in TOOLS:
        assert t.name not in nomes
        nomes.add(t.name)
        assert t.parameters["type"] == "object"
        for req in t.parameters["required"]:
            assert req in t.parameters["properties"]


def test_ferramenta_desconhecida():
    r = ToolExecutor().run("nao_existe", {})
    assert r["ok"] is False


def test_encerrar_processo_exige_confirmacao():
    proc = _spawn_sleeper()
    eventos = []
    ex = ToolExecutor(eventos.append)
    try:
        r = ex.run("encerrar_processo", {"alvo": str(proc.pid)})
        assert r["status"] == "aguardando_confirmacao"
        assert psutil.pid_exists(proc.pid)            # ainda NÃO foi encerrado
        pedido = next(e for e in eventos if e["type"] == "confirm_request")

        r = ex.resolve(pedido["id"], approve=True)
        assert r["ok"] is True
        proc.wait(timeout=5)
        assert proc.poll() is not None                 # agora sim, encerrado
    finally:
        if proc.poll() is None:
            proc.kill()


def test_recusar_confirmacao_mantem_processo():
    proc = _spawn_sleeper()
    ex = ToolExecutor()
    try:
        ex.run("encerrar_processo", {"alvo": str(proc.pid)})
        r = ex.resolve_latest(approve=False)
        assert r["ok"] is False
        assert proc.poll() is None
    finally:
        proc.kill()


def test_confirmacao_por_voz_no_assistente():
    proc = _spawn_sleeper()
    jarvis = Assistant(brain="local")
    try:
        resposta = jarvis.chat(f"encerre o processo {proc.pid}")
        assert "confirmação" in resposta.lower()
        assert proc.poll() is None
        jarvis.chat("sim")
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_processo_protegido_recusado():
    ex = ToolExecutor()
    r = ex.run("encerrar_processo", {"alvo": str(os.getpid())})   # o próprio JARVIS
    assert r["ok"] is False
    assert not ex.pending


def test_notas_em_arquivo():
    ex = ToolExecutor()
    assert ex.run("salvar_nota", {"texto": "comprar café"})["ok"]
    notas = ex.run("ler_notas", {"limite": 5})["notas"]
    assert any("comprar café" in n for n in notas)
    assert (settings.data_dir / "notas.md").is_file()


def test_analisar_diretorio(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x" * 1000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.log").write_bytes(b"y" * 5000)
    r = ToolExecutor().run("analisar_diretorio", {"caminho": str(tmp_path)})
    assert r["ok"] and r["arquivos"] == 2
    assert r["maiores"][0]["arquivo"].endswith("b.log")
    assert ".log" in r["por_extensao_mb"]


def test_relatorio_gerado():
    r = ToolExecutor().run("gerar_relatorio", {})
    caminho = Path(r["arquivo"])
    assert caminho.is_file()
    assert "RELATÓRIO" in caminho.read_text(encoding="utf-8")


def test_acoes_registradas_no_log():
    ToolExecutor().run("data_hora", {})
    log = settings.log_dir / "acoes.jsonl"
    assert log.is_file() and "data_hora" in log.read_text(encoding="utf-8")
