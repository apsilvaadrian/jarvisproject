"""Consulta de massivas do MHnet Notifier com um banco de exemplo (mesmo formato do massivas.db real)."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jarvis import massivas

ITENS = [
    {"id": "a1", "source": "automatic", "cidade": "Telêmaco Borba", "olt": "BRPR-TEB SLOT 11 PON 04",
     "slot_pon": "BRPR-TEB SLOT 11 PON 04", "data_queda": "21/10/2026", "horario_queda": "02:11",
     "status": "PENDENTE", "afetados": "5"},
    {"id": "a2", "source": "automatic", "cidade": "Itajaí", "slot_pon": "ITJ SLOT 1 PON 2", "status": "PENDENTE",
     "afetados": "762", "data_queda": "22/10/2026", "horario_queda": "10:00"},
    {"id": "a3", "source": "automatic", "cidade": "Caxias do Sul", "slot_pon": "CXS", "status": "RESOLVIDO",
     "afetados": "40"},
    {"id": "g1", "source": "grafana", "concentrador": "PGO", "olt": "PGO", "cidade": "",
     "cidades": ["PGO - Ponta Grossa", "IRI  - Irati"], "status": "QUEDA PPPoE", "afetados": "251",
     "queda_percentual": 1.44, "data_queda": "2026-10-23", "horario_queda": "00:40:00"},
    {"id": "n1", "source": "n2", "titulo": "Problema no SIG", "status": "PENDENTE", "prioridade": "ALERTA"},
]


@pytest.fixture
def banco(tmp_path, monkeypatch):
    def criar(minutos_atras=1):
        path = tmp_path / "massivas.db"
        path.unlink(missing_ok=True)
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE massivas (id TEXT PRIMARY KEY, payload TEXT, first_seen_at TEXT, "
                    "last_seen_at TEXT, viewed_at TEXT, remote_status TEXT)")
        visto = (datetime.now(timezone.utc) - timedelta(minutes=minutos_atras)).isoformat(timespec="seconds")
        for i in ITENS:
            con.execute("INSERT INTO massivas VALUES (?,?,?,?,?,?)",
                        (i["id"], json.dumps(i, ensure_ascii=False), visto, visto, None, i["status"]))
        con.commit()
        con.close()
        monkeypatch.setenv("MHNET_DB", str(path))
        return path
    return criar


def test_acha_por_cidade_sem_acento(banco):
    banco()
    r = massivas.consultar("telemaco borba")
    assert r["ok"] and r["encontradas"] == 1
    assert r["massivas"][0]["cidade"] == "Telêmaco Borba" and r["massivas"][0]["afetados"] == 5
    assert "aviso" not in r                                   # sincronizado há 1 min


def test_acha_cidade_atendida_por_concentrador(banco):
    banco()
    r = massivas.consultar("Ponta Grossa")
    assert r["encontradas"] == 1 and r["massivas"][0]["concentrador"] == "PGO"


def test_resolvidas_ficam_de_fora_por_padrao(banco):
    banco()
    assert massivas.consultar("Caxias do Sul")["encontradas"] == 0
    assert massivas.consultar("Caxias do Sul", incluir_resolvidas=True)["encontradas"] == 1


def test_lista_geral_ordenada_por_afetados_e_sem_avisos_n2(banco):
    banco()
    r = massivas.consultar()
    assert [m.get("cidade") or m.get("concentrador") for m in r["massivas"]] == ["Itajaí", "PGO", "Telêmaco Borba"]
    assert r["total_ativas_no_geral"] == 3
    assert massivas.consultar(incluir_avisos=True)["avisos_n2"][0]["titulo"] == "Problema no SIG"


def test_avisa_quando_o_notifier_parou_de_sincronizar(banco):
    banco(minutos_atras=90)
    assert "desatualizados" in massivas.consultar("Itajaí")["aviso"]


def test_sem_notifier_no_pc(tmp_path, monkeypatch):
    monkeypatch.setenv("MHNET_DB", str(tmp_path / "nao_existe.db"))
    r = massivas.consultar("Itajaí")
    assert not r["ok"] and "MHnet Notifier" in r["mensagem"]


def test_jarvis_offline_responde_massiva(banco):
    from jarvis.brain.local import LocalBrain
    from jarvis.tools import ToolExecutor
    banco()
    resposta = LocalBrain().respond([{"role": "user", "text": "Jarvis, tem massiva em Telêmaco Borba?"}], ToolExecutor())
    assert "1 massiva" in resposta and "5 afetados" in resposta
    resposta = LocalBrain().respond([{"role": "user", "text": "tem massiva em Curitiba?"}], ToolExecutor())
    assert "Não há massiva" in resposta


def test_numeros_do_report_ignoram_o_grafana(banco):
    banco()
    # a1 (5) e a2 (762) são massivas da planilha; a3 está resolvida; g1 é do Grafana; n1 é aviso N2
    assert massivas.para_report() == {"eventos": 2, "afetados": 767}
