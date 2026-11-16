"""Testes dos scripts de automação em Bash: scripts/run.sh e scripts/monitor.sh.

Cada teste monta um "projeto de mentira" numa pasta temporária e copia o script para lá. Como os scripts
descobrem a raiz do projeto pela própria localização (ROOT=.../..), tudo o que eles criam (run/, logs/,
reports/) fica na pasta temporária e o projeto de verdade não é tocado.

- Os testes marcados com `so_linux` precisam de /proc, ps, free, df, curl e sinais POSIX. No Windows são pulados.
- Os demais rodam também no Windows, usando o Bash que vem com o Git for Windows (Git Bash).
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
LINUX = sys.platform.startswith("linux")
so_linux = pytest.mark.skipif(not LINUX, reason="precisa de /proc, ps, free e sinais POSIX (Linux)")


def _bash() -> str | None:
    if sys.platform == "win32":
        # No Windows, "bash" pode ser o do WSL; usamos o Git Bash, que fica ao lado do git.exe
        git = shutil.which("git")
        if git:
            candidato = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            if candidato.is_file():
                return str(candidato)
        return None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="Bash não encontrado")


def _projeto(tmp_path: Path) -> Path:
    """Cria a pasta de um projeto falso com os dois scripts."""
    (tmp_path / "scripts").mkdir()
    for nome in ("run.sh", "monitor.sh"):
        shutil.copy2(RAIZ / "scripts" / nome, tmp_path / "scripts" / nome)
    return tmp_path


def _rodar(projeto: Path, script: str, *args: str, env: dict | None = None, timeout: int = 40):
    return subprocess.run([BASH, (projeto / "scripts" / script).as_posix(), *args], cwd=projeto,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)


def _pid_morto() -> int:
    """PID de um processo que acabou de terminar (não existe mais)."""
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _vivo(pid: int) -> bool:
    try:
        os.kill(pid, 0)                          # sinal 0: só pergunta ao SO se o processo existe
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _esperar_morrer(pid: int, segundos: float = 6) -> bool:
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        if not _vivo(pid):
            return True
        time.sleep(0.1)
    return False


# --------------------------------------------------------------------------- rodam em qualquer SO

@pytest.mark.parametrize("script", ["run.sh", "monitor.sh"])
def test_sintaxe_bash_valida(script):
    r = subprocess.run([BASH, "-n", (RAIZ / "scripts" / script).as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr          # bash -n: só lê o script e acusa erro de sintaxe


@pytest.mark.parametrize("script", ["run.sh", "monitor.sh"])
def test_scripts_tem_permissao_de_execucao_no_git(script):
    if not shutil.which("git"):
        pytest.skip("git não encontrado")
    r = subprocess.run(["git", "ls-files", "-s", f"scripts/{script}"], cwd=RAIZ, capture_output=True, text=True)
    if not r.stdout:
        pytest.skip("fora de um repositório Git")
    assert r.stdout.split()[0] == "100755"       # o Git guarda o bit de execução (chmod +x)


def test_run_opcao_invalida_mostra_uso_e_sai_com_2(tmp_path):
    r = _rodar(_projeto(tmp_path), "run.sh", "voar")
    assert r.returncode == 2
    assert "Uso:" in r.stdout


def test_monitor_opcao_invalida_sai_com_2(tmp_path):
    r = _rodar(_projeto(tmp_path), "monitor.sh", "--limite-xyz", "5")
    assert r.returncode == 2
    assert "Opção desconhecida" in r.stdout


@pytest.mark.parametrize("args", [["--limite-cpu"], ["--limite-cpu", "abc"], ["--limite-mem", "-5"]])
def test_monitor_limite_sem_numero_inteiro_sai_com_2(tmp_path, args):
    r = _rodar(_projeto(tmp_path), "monitor.sh", *args)
    assert r.returncode == 2                     # 2 = uso errado (1 fica reservado para "houve alerta")
    assert "Valor inválido" in r.stderr


def test_run_status_sem_arquivo_de_pid(tmp_path):
    projeto = _projeto(tmp_path)
    r = _rodar(projeto, "run.sh", "status")
    assert r.returncode == 0
    assert "JARVIS parado." in r.stdout
    assert (projeto / "run").is_dir() and (projeto / "logs").is_dir()   # o script cria as pastas


def test_run_stop_com_pid_antigo_limpa_o_arquivo(tmp_path):
    projeto = _projeto(tmp_path)
    (projeto / "run").mkdir()
    pidfile = projeto / "run" / "jarvis.pid"
    pidfile.write_text(str(_pid_morto()))        # sobrou de uma execução que caiu
    r = _rodar(projeto, "run.sh", "stop")
    assert r.returncode == 0
    assert "não está em execução" in r.stdout
    assert not pidfile.exists()


# --------------------------------------------------------------------------- run.sh no Linux

SERVIDOR_FALSO = '''
import json, os
from http.server import BaseHTTPRequestHandler, HTTPServer

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        corpo = json.dumps({"status": "online", "pid": os.getpid()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *args):
        pass

HTTPServer((os.environ["JARVIS_HOST"], int(os.environ["JARVIS_PORT"])), Health).serve_forever()
'''


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@so_linux
@pytest.mark.skipif(not (shutil.which("curl") and shutil.which("python3")), reason="precisa de curl e python3")
def test_run_start_status_stop_com_jarvis_falso(tmp_path):
    projeto = _projeto(tmp_path)
    # JARVIS falso: o run.sh executa "python3 -m jarvis" com PYTHONPATH=<raiz>/src
    pacote = projeto / "src" / "jarvis"
    pacote.mkdir(parents=True)
    (pacote / "__init__.py").write_text("")
    (pacote / "__main__.py").write_text(SERVIDOR_FALSO)
    porta = _porta_livre()
    # A porta vem do .env: o run.sh carrega o arquivo como variáveis de ambiente (set -a; source .env)
    (projeto / ".env").write_text(f"JARVIS_HOST=127.0.0.1\nJARVIS_PORT={porta}\n")
    # xdg-open falso, para o teste não abrir o navegador de verdade
    bin_falso = projeto / "bin-falso"
    bin_falso.mkdir()
    (bin_falso / "xdg-open").write_text("#!/bin/sh\nexit 0\n")
    (bin_falso / "xdg-open").chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_falso}:{os.environ['PATH']}"}
    pidfile = projeto / "run" / "jarvis.pid"

    pid = None
    try:
        r = _rodar(projeto, "run.sh", "start", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "JARVIS online" in r.stdout
        pid = int(pidfile.read_text())
        assert _vivo(pid)
        # No Linux o PID gravado é o do próprio servidor (no Windows, a .venv cria um processo filho)
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/health", timeout=3) as resp:
            assert json.load(resp)["pid"] == pid

        r = _rodar(projeto, "run.sh", "status", env=env)
        assert f"PID {pid}" in r.stdout
        assert "STAT" in r.stdout                # cabeçalho do ps -o pid,ppid,stat,...

        r = _rodar(projeto, "run.sh", "start", env=env)
        assert "já está em execução" in r.stdout  # não sobe um segundo servidor

        r = _rodar(projeto, "run.sh", "stop", env=env)
        assert "SIGTERM" in r.stdout
        assert "JARVIS encerrado." in r.stdout
        assert _esperar_morrer(pid)
        assert not pidfile.exists()
    finally:
        if pid and _vivo(pid):
            os.kill(pid, signal.SIGKILL)


@so_linux
def test_run_stop_usa_sigkill_quando_o_processo_ignora_sigterm(tmp_path):
    projeto = _projeto(tmp_path)
    teimoso = subprocess.Popen(
        [sys.executable, "-c",
         "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('pronto', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert teimoso.stdout.readline().strip() == "pronto"   # só segue depois que o SIGTERM foi ignorado
        (projeto / "run").mkdir()
        (projeto / "run" / "jarvis.pid").write_text(str(teimoso.pid))

        r = _rodar(projeto, "run.sh", "stop")
        assert "enviando SIGKILL" in r.stdout
        teimoso.wait(timeout=5)
        assert teimoso.returncode == -signal.SIGKILL           # morto pelo sinal 9
        assert not (projeto / "run" / "jarvis.pid").exists()
    finally:
        if teimoso.poll() is None:
            teimoso.kill()


# --------------------------------------------------------------------------- monitor.sh no Linux

def _relatorios(projeto: Path) -> list[Path]:
    return sorted((projeto / "reports").glob("monitor_*.txt"))


@so_linux
def test_monitor_sem_alerta_gera_relatorio_e_sai_com_0(tmp_path):
    projeto = _projeto(tmp_path)
    r = _rodar(projeto, "monitor.sh", "--limite-cpu", "101", "--limite-mem", "101", "--limite-disco", "101")
    assert r.returncode == 0, r.stdout + r.stderr
    [relatorio] = _relatorios(projeto)
    texto = relatorio.read_text(encoding="utf-8")
    for secao in ("[SISTEMA]", "[CPU]", "[MEMÓRIA]", "[DISCOS]", "[TOP 5 PROCESSOS"):
        assert secao in texto
    assert "Alertas emitidos: 0" in texto
    assert relatorio.stat().st_mode & 0o777 == 0o640   # chmod 640: dono lê/escreve, grupo só lê, outros nada


@so_linux
def test_monitor_com_alerta_grava_log_e_sai_com_1(tmp_path):
    projeto = _projeto(tmp_path)
    r = _rodar(projeto, "monitor.sh", "--limite-cpu", "0", "--limite-mem", "0", "--limite-disco", "0")
    assert r.returncode == 1                     # o cron/automação enxerga o alerta pelo código de saída
    alertas = (projeto / "logs" / "alertas.log").read_text(encoding="utf-8")
    assert "ALERTA CPU" in alertas
    assert "ALERTA Memória" in alertas


@so_linux
def test_monitor_mantem_so_os_30_relatorios_mais_recentes(tmp_path):
    projeto = _projeto(tmp_path)
    (projeto / "reports").mkdir()
    antigo = time.time() - 10 * 86400
    for i in range(35):
        f = projeto / "reports" / f"monitor_20200101_0000{i:02d}.txt"
        f.write_text("relatório antigo")
        os.utime(f, (antigo + i, antigo + i))    # datas diferentes: o mais antigo é o _000000

    r = _rodar(projeto, "monitor.sh", "--limite-cpu", "101", "--limite-mem", "101", "--limite-disco", "101")
    assert r.returncode == 0
    nomes = [f.name for f in _relatorios(projeto)]
    assert len(nomes) == 30
    assert "monitor_20200101_000000.txt" not in nomes      # os mais antigos foram apagados
    assert any(not n.startswith("monitor_2020") for n in nomes)   # o relatório novo ficou
