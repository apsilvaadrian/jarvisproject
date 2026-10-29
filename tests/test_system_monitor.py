"""Verifica a coleta de recursos do sistema (CPU, memória, disco, processos)."""
import os

from jarvis import system_monitor as sm


def test_cpu_percent_dentro_do_intervalo():
    cpu = sm.cpu_info()
    assert 0 <= cpu["percent"] <= 100
    assert cpu["nucleos_logicos"] >= 1
    assert len(cpu["por_nucleo"]) == cpu["nucleos_logicos"]


def test_memoria_coerente():
    mem = sm.memory_info()
    assert mem["total_gb"] > 0
    assert 0 <= mem["percent"] <= 100
    assert mem["usado_gb"] <= mem["total_gb"]


def test_discos_listados():
    discos = sm.disk_info()
    assert discos, "deve existir ao menos uma partição"
    for d in discos:
        assert d["livre_gb"] <= d["total_gb"]


def test_processo_atual_aparece_na_lista():
    procs = sm.list_processes("memoria", limit=10_000)
    assert any(p["pid"] == os.getpid() for p in procs)
    # ordenado por memória (decrescente)
    mems = [p["memoria_mb"] for p in procs]
    assert mems == sorted(mems, reverse=True)


def test_filtro_por_nome():
    procs = sm.list_processes("cpu", limit=50, name_filter="python")
    assert all("python" in p["nome"].lower() for p in procs)


def test_snapshot_completo():
    s = sm.snapshot()
    for key in ("cpu", "memoria", "discos", "rede", "temperatura", "sistema", "processos"):
        assert key in s
    assert s["sistema"]["total_processos"] > 0


def test_resumo_em_texto():
    texto = sm.summary_text()
    assert "CPU" in texto and "memória" in texto
