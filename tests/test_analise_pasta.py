"""Análise de pasta (ferramenta analisar_diretorio): casos de erro e situações de borda.

O teste do caminho feliz já existe em test_tools.py. Aqui ficam os casos em que algo dá errado:
pasta que não existe, arquivo no lugar de pasta, falta de permissão, pasta grande demais etc.
Em todos eles a ferramenta deve devolver uma resposta, nunca derrubar o JARVIS com uma exceção.
"""
import json
import os
import sys
import types
from pathlib import Path

import pytest

from jarvis import os_control
from jarvis.assistant import Assistant
from jarvis.config import settings
from jarvis.tools import ToolExecutor


def _analisar(caminho) -> dict:
    return ToolExecutor().run("analisar_diretorio", {"caminho": str(caminho)})


def test_pasta_inexistente_retorna_erro_sem_excecao(tmp_path: Path):
    r = _analisar(tmp_path / "nao_existe")
    assert r["ok"] is False
    assert "não encontrado" in r["mensagem"]


def test_arquivo_no_lugar_de_pasta(tmp_path: Path):
    arquivo = tmp_path / "relatorio.txt"
    arquivo.write_text("conteúdo")
    r = _analisar(arquivo)
    assert r["ok"] is False                      # um arquivo não é um diretório
    assert "não encontrado" in r["mensagem"]


def test_pasta_vazia(tmp_path: Path):
    r = _analisar(tmp_path)
    assert r["ok"] is True
    assert r["arquivos"] == 0
    assert r["total_mb"] == 0
    assert r["maiores"] == []
    assert r["por_extensao_mb"] == {}
    assert r["sem_permissao"] == 0


def test_ignora_pastas_ocultas_e_node_modules(tmp_path: Path):
    (tmp_path / "visivel.txt").write_text("a")
    for pasta in (".git", "node_modules"):
        (tmp_path / pasta).mkdir()
        (tmp_path / pasta / "dentro.txt").write_text("b")
    r = _analisar(tmp_path)
    assert r["arquivos"] == 1
    assert r["maiores"][0]["arquivo"].endswith("visivel.txt")


def test_arquivo_sem_extensao_tem_grupo_proprio(tmp_path: Path):
    (tmp_path / "Makefile").write_bytes(b"x" * 2_000_000)
    r = _analisar(tmp_path)
    assert "(sem extensão)" in r["por_extensao_mb"]


def test_arquivo_sem_permissao_e_contado_e_a_analise_continua(tmp_path: Path, monkeypatch):
    (tmp_path / "liberado.txt").write_text("ok")
    (tmp_path / "bloqueado.txt").write_text("segredo")

    # Simula o SO negando o acesso (EACCES) a um arquivo. A troca é feita SÓ no módulo os_control,
    # sem mexer no os.stat do processo inteiro (outras threads continuam usando o verdadeiro).
    stat_real = os.stat

    def stat_negado(caminho, *args, **kwargs):
        if str(caminho).endswith("bloqueado.txt"):
            raise PermissionError(13, "Permissão negada", str(caminho))
        return stat_real(caminho, *args, **kwargs)

    os_falso = types.SimpleNamespace(**{n: getattr(os, n) for n in dir(os) if not n.startswith("__")})
    os_falso.stat = stat_negado
    monkeypatch.setattr(os_control, "os", os_falso)

    r = _analisar(tmp_path)
    assert r["ok"] is True
    assert r["arquivos"] == 1                    # só o arquivo liberado entra na conta
    assert r["sem_permissao"] == 1               # o bloqueado é contado à parte
    assert all("bloqueado" not in m["arquivo"] for m in r["maiores"])


@pytest.mark.skipif(sys.platform == "win32", reason="chmod com permissões POSIX só funciona no Linux/macOS")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="o root ignora as permissões")
def test_subpasta_sem_permissao_de_leitura_no_linux(tmp_path: Path):
    (tmp_path / "aberto.txt").write_text("ok")
    trancada = tmp_path / "trancada"
    trancada.mkdir()
    (trancada / "escondido.txt").write_text("x")
    trancada.chmod(0o000)                        # equivalente a: chmod 000 trancada
    try:
        r = _analisar(tmp_path)
        assert r["ok"] is True                   # não quebra: pula a pasta que não consegue ler
        assert r["arquivos"] == 1
    finally:
        trancada.chmod(0o755)                    # devolve a permissão para o pytest conseguir apagar


def test_pasta_grande_demais_devolve_resultado_parcial(tmp_path: Path, monkeypatch):
    for i in range(3):
        sub = tmp_path / f"sub{i}"
        sub.mkdir()
        (sub / "arquivo.txt").write_text("x")

    # Relógio falso: a primeira leitura marca o início e as seguintes já passaram do limite de 8 s.
    # Assim o teste não precisa de uma pasta gigante nem esperar de verdade.
    leituras = iter([0.0])
    relogio = types.SimpleNamespace(monotonic=lambda: next(leituras, 1000.0))
    monkeypatch.setattr(os_control, "time", relogio)

    r = _analisar(tmp_path)
    assert r["ok"] is True
    assert r["parcial"] is True
    assert r["arquivos"] < 3                     # parou antes de percorrer tudo


def test_til_aponta_para_a_pasta_do_usuario(tmp_path: Path, monkeypatch):
    (tmp_path / "nota.txt").write_text("oi")
    # O "~" vem de variáveis de ambiente: HOME no Linux, USERPROFILE no Windows
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    r = _analisar("~")
    assert r["ok"] is True
    assert Path(r["diretorio"]) == tmp_path.resolve()
    assert r["arquivos"] == 1


def test_link_simbolico_nao_conta_o_arquivo_de_destino(tmp_path: Path):
    grande = tmp_path / "fora" / "grande.bin"
    grande.parent.mkdir()
    grande.write_bytes(b"x" * 3_000_000)
    pasta = tmp_path / "pasta"
    pasta.mkdir()
    try:
        (pasta / "atalho.bin").symlink_to(grande)
    except (OSError, NotImplementedError):
        pytest.skip("criar link simbólico exige permissão especial neste Windows")
    r = _analisar(pasta)
    assert r["ok"] is True
    assert r["arquivos"] == 1
    assert r["total_mb"] < 1                     # mede o link, não os 3 MB do arquivo de destino


def test_erro_fica_registrado_no_log_de_auditoria(tmp_path: Path):
    _analisar(tmp_path / "nao_existe")
    ultima = (settings.log_dir / "acoes.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    registro = json.loads(ultima)
    assert registro["ferramenta"] == "analisar_diretorio"
    assert registro["sucesso"] is False


def test_cerebro_local_explica_o_erro_ao_usuario(tmp_path: Path):
    resposta = Assistant(brain="local").chat(f"analise a pasta {tmp_path / 'nao_existe'}")
    assert "não encontrado" in resposta.lower()
