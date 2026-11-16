"""Testes do scripts/backup.sh: backup de data/ e logs/ com data no nome e rotação dos antigos.

Igual aos testes do run.sh/monitor.sh: o script é copiado para um projeto falso numa pasta temporária, então
os backups criados aqui nunca se misturam com os de verdade.

- Validação das opções: roda em qualquer SO com Bash (no Windows, o Git Bash).
- Backup de verdade: precisa do comando `flock` (util-linux), que existe no Linux e não existe no Git Bash.
- Permissões (umask 077, chmod) e trava concorrente: só no Linux.
"""
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
POSIX = sys.platform != "win32"


def _bash() -> str | None:
    if sys.platform == "win32":
        git = shutil.which("git")
        if git:
            candidato = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            if candidato.is_file():
                return str(candidato)
        return None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="Bash não encontrado")


def _tem_flock() -> bool:
    if BASH is None:
        return False
    return subprocess.run([BASH, "-c", "command -v flock"], capture_output=True).returncode == 0


precisa_flock = pytest.mark.skipif(not _tem_flock(), reason="precisa do comando flock (Linux)")
so_posix = pytest.mark.skipif(not POSIX, reason="permissões POSIX só no Linux/macOS")
nao_root = pytest.mark.skipif(POSIX and os.geteuid() == 0, reason="o root ignora as permissões")


def _projeto(tmp_path: Path, com_dados: bool = True) -> Path:
    projeto = tmp_path / "jarvis"
    (projeto / "scripts").mkdir(parents=True)
    shutil.copy2(RAIZ / "scripts" / "backup.sh", projeto / "scripts" / "backup.sh")
    if com_dados:
        (projeto / "data").mkdir()
        (projeto / "data" / "notas.md").write_text("- [16/11/2026 10:00] ligar para a Bia Souza\n", encoding="utf-8")
        (projeto / "logs").mkdir()
        (projeto / "logs" / "jarvis.log").write_text("servidor iniciado\n", encoding="utf-8")
    return projeto


def _rodar(projeto: Path, *args: str, env_extra: dict | None = None):
    env = dict(os.environ)
    env.pop("JARVIS_BACKUP_DIR", None)           # um destino configurado na máquina não pode vazar para o teste
    env.update(env_extra or {})
    return subprocess.run([BASH, (projeto / "scripts" / "backup.sh").as_posix(), *args], cwd=projeto,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)


def _backups(pasta: Path) -> list[Path]:
    return sorted(pasta.glob("jarvis_*.tar.gz"))


# --------------------------------------------------------------------------- qualquer SO

def test_sintaxe_bash_valida():
    r = subprocess.run([BASH, "-n", (RAIZ / "scripts" / "backup.sh").as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_backup_tem_permissao_de_execucao_no_git():
    if not shutil.which("git"):
        pytest.skip("git não encontrado")
    r = subprocess.run(["git", "ls-files", "-s", "scripts/backup.sh"], cwd=RAIZ, capture_output=True, text=True)
    if not r.stdout:
        pytest.skip("fora de um repositório Git")
    assert r.stdout.split()[0] == "100755"


@pytest.mark.parametrize("args", [["--manter"], ["--manter", "0"], ["--manter", "abc"], ["--manter", "-3"]])
def test_manter_invalido_sai_com_2_sem_criar_nada(tmp_path, args):
    projeto = _projeto(tmp_path)
    r = _rodar(projeto, *args)
    assert r.returncode == 2
    assert "Valor inválido para --manter" in r.stderr
    assert not (projeto / "backups").exists()    # valida ANTES de mexer no disco


def test_opcao_desconhecida_sai_com_2(tmp_path):
    r = _rodar(_projeto(tmp_path), "--tudo")
    assert r.returncode == 2
    assert "Opção desconhecida" in r.stderr
    assert "Uso:" in r.stderr


# --------------------------------------------------------------------------- backup de verdade (flock)

@precisa_flock
def test_backup_guarda_data_e_logs_e_pode_ser_restaurado(tmp_path):
    projeto = _projeto(tmp_path)
    r = _rodar(projeto)
    assert r.returncode == 0, r.stdout + r.stderr

    [arquivo] = _backups(projeto / "backups")
    assert not list((projeto / "backups").glob(".*.parcial"))      # nenhum arquivo pela metade

    with tarfile.open(arquivo, "r:gz") as tar:  # o mesmo formato que o "tar -xzf" lê para restaurar
        nomes = tar.getnames()
        assert "data/notas.md" in nomes and "logs/jarvis.log" in nomes
        conteudo = tar.extractfile("data/notas.md").read()
    assert conteudo == (projeto / "data" / "notas.md").read_bytes()

    log = (projeto / "logs" / "backup.log").read_text(encoding="utf-8")
    assert " OK " in log and arquivo.name in log


@precisa_flock
@so_posix
def test_backup_nasce_so_para_o_dono(tmp_path):
    projeto = _projeto(tmp_path)
    assert _rodar(projeto).returncode == 0
    [arquivo] = _backups(projeto / "backups")
    assert arquivo.stat().st_mode & 0o777 == 0o600                 # umask 077: rw------- (só o dono)
    assert (projeto / "backups").stat().st_mode & 0o777 == 0o700   # rwx------


@precisa_flock
def test_destino_vem_da_variavel_de_ambiente(tmp_path):
    projeto = _projeto(tmp_path)
    destino = tmp_path / "hd-externo"
    r = _rodar(projeto, env_extra={"JARVIS_BACKUP_DIR": str(destino)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_backups(destino)) == 1
    assert not (projeto / "backups").exists()


@precisa_flock
def test_rotacao_mantem_so_os_mais_recentes(tmp_path):
    projeto = _projeto(tmp_path)
    pasta = projeto / "backups"
    pasta.mkdir()
    for dia in range(1, 6):                      # 5 backups antigos, de 01 a 05/01/2020
        (pasta / f"jarvis_2020-01-0{dia}_020000.tar.gz").write_bytes(b"antigo")

    r = _rodar(projeto, "--manter", "3")
    assert r.returncode == 0, r.stdout + r.stderr
    nomes = [f.name for f in _backups(pasta)]
    assert len(nomes) == 3
    assert "jarvis_2020-01-05_020000.tar.gz" in nomes and "jarvis_2020-01-04_020000.tar.gz" in nomes
    assert "jarvis_2020-01-01_020000.tar.gz" not in nomes          # os mais antigos saíram
    assert any(not n.startswith("jarvis_2020") for n in nomes)     # o backup novo ficou
    assert (projeto / "logs" / "backup.log").read_text(encoding="utf-8").count("Rotação: apagado") == 3


@precisa_flock
@so_posix
def test_segundo_backup_simultaneo_desiste(tmp_path):
    import fcntl

    projeto = _projeto(tmp_path)
    (projeto / "run").mkdir()
    # Este teste segura a mesma trava que o script usa, como se um backup anterior ainda estivesse rodando
    with open(projeto / "run" / "backup.lock", "w") as trava:
        fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = _rodar(projeto)
    assert r.returncode == 1
    assert "Outro backup já está em andamento" in r.stderr
    assert not _backups(projeto / "backups")


@precisa_flock
@so_posix
@nao_root
def test_falha_do_tar_nao_deixa_arquivo_pela_metade(tmp_path):
    projeto = _projeto(tmp_path)
    destino = tmp_path / "sem-escrita"
    destino.mkdir()
    destino.chmod(0o500)                         # dá para entrar e listar, mas não para criar arquivos
    try:
        r = _rodar(projeto, env_extra={"JARVIS_BACKUP_DIR": str(destino)})
        assert r.returncode == 1
        assert "ERRO o tar falhou" in (projeto / "logs" / "backup.log").read_text(encoding="utf-8")
        assert list(destino.iterdir()) == []     # nem o backup nem o .parcial ficaram lá
    finally:
        destino.chmod(0o700)
