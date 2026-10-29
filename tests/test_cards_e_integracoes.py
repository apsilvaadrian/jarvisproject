"""Controle dos cards (voz e tela), integrações (agenda iCal, GitHub, Docker, Gmail) e os novos provedores de IA."""
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

import jarvis.config as cfg
from jarvis import cards, integracoes, server
from jarvis.brain import filter_models
from jarvis.brain.local import LocalBrain
from jarvis.tools import ToolExecutor

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def layout_limpo(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    integracoes.limpar_cache()


# --------------------------------------------------------------------------- cards
def test_layout_padrao_tem_todos_os_cards_uma_vez():
    layout = cards.carregar()
    ids = [c for z in cards.ZONAS for c in layout["zonas"][z]]
    assert sorted(ids) == sorted(cards.CATALOGO) and len(ids) == len(set(ids))
    assert set(layout["ocultos"]) == {"github", "docker"}


def test_nomes_falados_viram_cards_e_colunas():
    assert cards.achar_card("o card do Spotify") == "musica"
    assert cards.achar_card("memória") == "memoria"
    assert cards.achar_card("e-mail") == "email"
    assert cards.achar_card("card de processos") == "processos"
    assert cards.achar_card("geladeira") is None
    assert cards.achar_zona("extrema direita") == "extra"
    assert cards.achar_zona("coluna da direita") == "direita"
    assert cards.achar_zona("lado esquerdo") == "esquerda"


def test_mover_fixar_ocultar_e_restaurar():
    r = cards.controlar("mover", "processos", "esquerda", "topo")
    assert r["ok"] and r["layout"]["zonas"]["esquerda"][0] == "processos"
    assert "processos" not in r["layout"]["zonas"]["direita"]
    r = cards.controlar("mover", "spotify", "extra", "2")
    assert r["layout"]["zonas"]["extra"][1] == "musica"
    assert cards.controlar("fixar", "alertas")["layout"]["fixados"] == ["alertas"]
    r = cards.controlar("ocultar", "alertas")
    assert "alertas" in r["layout"]["ocultos"] and r["layout"]["fixados"] == []    # oculto deixa de ser fixado
    assert "github" not in cards.controlar("mostrar", "github")["layout"]["ocultos"]
    assert cards.carregar()["zonas"]["esquerda"][0] == "processos"                    # ficou salvo no arquivo
    assert cards.controlar("restaurar")["layout"] == cards.normalizar(cards.PADRAO)


def test_card_desconhecido_explica_quais_existem():
    r = cards.controlar("fixar", "geladeira")
    assert not r["ok"] and "Spotify" in r["mensagem"]


def test_layout_vindo_da_tela_e_normalizado():
    """A tela manda o layout depois de arrastar; ids repetidos ou desconhecidos são descartados."""
    r = client.post("/api/cards", json={"zonas": {"esquerda": ["rede", "rede", "xyz"], "direita": [], "extra": []},
                                        "fixados": ["rede", "xyz"], "ocultos": []})
    layout = r.json()["layout"]
    assert layout["zonas"]["esquerda"][0] == "rede" and layout["zonas"]["esquerda"].count("rede") == 1
    assert layout["fixados"] == ["rede"]
    ids = [c for z in cards.ZONAS for c in layout["zonas"][z]]
    assert sorted(ids) == sorted(cards.CATALOGO)                       # os que faltaram voltam à coluna padrão
    assert client.post("/api/cards", json={"restaurar": True}).json()["layout"]["fixados"] == []


def test_ferramenta_avisa_a_tela_com_o_layout_novo():
    eventos = []
    ToolExecutor(eventos.append).run("controlar_cards", {"acao": "fixar", "card": "spotify"})
    painel = next(e for e in eventos if e["type"] == "painel")
    assert painel["tipo"] == "cards" and painel["dados"]["card"] == "musica"
    assert painel["dados"]["layout"]["fixados"] == ["musica"]


@pytest.mark.parametrize("frase, args", [
    ("fixa o card do spotify", {"acao": "fixar", "card": "spotify"}),
    ("desafixa os processos", {"acao": "desafixar", "card": "processos"}),
    ("esconde a rede", {"acao": "ocultar", "card": "rede"}),
    ("mostra o card do github", {"acao": "mostrar", "card": "github"}),
    ("move o card de processos para a esquerda", {"acao": "mover", "card": "processos", "zona": "esquerda"}),
    ("coloca a agenda na coluna da direita no topo",
     {"acao": "mover", "card": "agenda", "zona": "coluna da direita", "posicao": "topo"}),
    ("restaura os cards", {"acao": "restaurar"}),
])
def test_jarvis_offline_entende_pedidos_de_cards(frase, args):
    chamadas = []

    class Ex(ToolExecutor):
        def run(self, nome, a):
            chamadas.append((nome, a))
            return {"ok": True, "mensagem": "ok"}

    LocalBrain().respond([{"role": "user", "text": frase}], Ex())
    assert chamadas == [("controlar_cards", args)]


def test_musica_e_fechar_app_continuam_funcionando():
    chamadas = []

    class Ex(ToolExecutor):
        def run(self, nome, a):
            chamadas.append(nome)
            return {"ok": True, "mensagem": "ok"}

    LocalBrain().respond([{"role": "user", "text": "coloca a playlist rock classico"}], Ex())
    LocalBrain().respond([{"role": "user", "text": "fecha o chrome"}], Ex())
    assert chamadas == ["spotify_tocar", "encerrar_processo"]


# --------------------------------------------------------------------------- agenda (iCal)
def _ics(hoje):
    d = hoje.strftime("%Y%m%d")
    return f"""BEGIN:VCALENDAR
VERSION:2.0
X-WR-CALNAME:Trabalho
BEGIN:VEVENT
UID:1
SUMMARY:Daily do time
DTSTART:{d}T090000
DTEND:{d}T091500
RRULE:FREQ=DAILY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:2
SUMMARY:Aniversário da Ana
DTSTART;VALUE=DATE:{d}
DTEND;VALUE=DATE:{(hoje + timedelta(days=1)).strftime("%Y%m%d")}
END:VEVENT
END:VCALENDAR""".replace("\n", "\r\n").encode()


def test_le_ics_com_repeticao_e_dia_inteiro():
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    evs = integracoes.eventos_do_ics(_ics(hoje), hoje, hoje + timedelta(days=7))
    daily = [e for e in evs if e["titulo"] == "Daily do time"]
    assert len(daily) == 3 and daily[0]["inicio"].endswith("09:00:00") and daily[0]["agenda"] == "Trabalho"
    assert any(e["dia_inteiro"] and e["titulo"] == "Aniversário da Ana" for e in evs)


def test_agenda_pela_url_secreta(monkeypatch):
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(cfg.settings, "agenda_ics", "webcal://calendar.google.com/x/basic.ics")
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        return httpx.Response(200, content=_ics(hoje), request=httpx.Request("GET", url))

    monkeypatch.setattr(integracoes.httpx, "get", fake_get)
    r = integracoes.agenda(3)
    assert r["ok"] and urls == ["https://calendar.google.com/x/basic.ics"]      # webcal:// vira https://
    assert {e["titulo"] for e in r["eventos"]} >= {"Aniversário da Ana"}


def test_sem_integracoes_os_cards_pedem_para_conectar():
    for fn in (lambda: integracoes.agenda(1), lambda: integracoes.email(3), integracoes.github):
        r = fn()
        assert r["ok"] is False and r["conectado"] is False and "Integrações" in r["mensagem"]


# --------------------------------------------------------------------------- GitHub e Docker
def test_github_resumo(monkeypatch):
    monkeypatch.setattr(cfg.settings, "github_token", "ghp_teste")
    respostas = {
        "/user": {"login": "adrian", "name": "Adrian"},
        "/notifications": [{}, {}, {}],
        "/user/repos": [{"full_name": "adrian/jarvis", "html_url": "u", "pushed_at": "2026-10-26T10:00:00Z", "private": True}],
    }

    def fake_gh(caminho, **params):
        if caminho == "/search/issues":
            item = {"title": "Novo card", "repository_url": "https://api.github.com/repos/adrian/jarvis",
                    "html_url": "pr", "number": 7}
            return {"total_count": 1, "items": [item]}
        return respostas[caminho]

    monkeypatch.setattr(integracoes, "_gh", fake_gh)
    r = integracoes.github()
    assert r["ok"] and r["usuario"] == "adrian" and r["notificacoes"] == 3
    assert r["meus_prs"][0]["repo"] == "adrian/jarvis" and r["repos"][0]["privado"]


def test_docker_ausente_e_presente(monkeypatch):
    monkeypatch.setattr(integracoes.shutil, "which", lambda n: None)
    assert "não está instalado" in integracoes.docker()["mensagem"]
    monkeypatch.setattr(integracoes.shutil, "which", lambda n: "docker")
    saida = '{"Names":"db","Image":"postgres","State":"running","Status":"Up 2h","Ports":""}\n' \
            '{"Names":"web","Image":"nginx","State":"exited","Status":"Exited","Ports":""}'
    monkeypatch.setattr(integracoes.subprocess, "run",
                        lambda *a, **k: integracoes.subprocess.CompletedProcess(a, 0, saida, ""))
    r = integracoes.docker()
    assert r["ok"] and r["rodando"] == 1 and [c["nome"] for c in r["containers"]] == ["db", "web"]


def test_parar_container_pede_confirmacao():
    eventos = []
    r = ToolExecutor(eventos.append).run("docker_controlar", {"container": "db", "acao": "parar"})
    assert r["status"] == "aguardando_confirmacao" and "Parar o container db" in r["pergunta"]


# --------------------------------------------------------------------------- e-mail (IMAP só leitura)
class FakeIMAP:
    def __init__(self, *a, **k):
        self.readonly = None

    def login(self, user, senha):
        if senha != "app-senha":
            raise integracoes.imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    def select(self, caixa, readonly=False):
        self.readonly = readonly
        return "OK", [b"2"]

    def search(self, *a):
        return "OK", [b"1 2"]

    def fetch(self, mid, partes):
        assert "PEEK" in partes                                           # nunca marca como lido
        bruto = (b"From: =?utf-8?q?Jo=C3=A3o?= <joao@x.com>\r\nSubject: Reuni\xc3\xa3o amanh\xc3\xa3\r\n"
                 b"Date: Mon, 26 Oct 2026 10:00:00 -0300\r\n\r\n")
        return "OK", [(b"1", bruto)]

    def logout(self):
        pass


def test_email_nao_lidos_sem_marcar_como_lido(monkeypatch):
    monkeypatch.setattr(integracoes.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(cfg.settings, "email_usuario", "eu@gmail.com")
    monkeypatch.setattr(cfg.settings, "email_senha", "app-senha")
    r = integracoes.email(5)
    assert r["ok"] and r["nao_lidos"] == 2 and r["mensagens"][0]["de"] == "João"


def test_senha_recusada_nao_fica_salva(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(integracoes.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(cfg.settings, "email_usuario", "")
    monkeypatch.setattr(cfg.settings, "email_senha", "")
    r = client.post("/api/integracoes/email", json={"campos": {"EMAIL_USUARIO": "eu@gmail.com", "EMAIL_SENHA_APP": "errada"}})
    assert r.status_code == 400 and "recusou" in r.json()["detail"]
    assert cfg.settings.email_senha == ""
    r = client.post("/api/integracoes/email", json={"campos": {"EMAIL_USUARIO": "eu@gmail.com", "EMAIL_SENHA_APP": "app-senha"}})
    assert r.status_code == 200 and r.json()["ok"]
    assert next(i for i in r.json()["integracoes"] if i["id"] == "email")["conectado"]


def test_integracao_so_aceita_os_proprios_campos(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    r = client.post("/api/integracoes/github", json={"campos": {"ANTHROPIC_API_KEY": "roubada"}})
    assert r.status_code == 400
    assert client.post("/api/integracoes/spotify", json={"campos": {"X": "1"}}).status_code == 400


def test_lista_de_integracoes_e_alertas():
    ids = {i["id"] for i in client.get("/api/integracoes").json()["integracoes"]}
    assert {"email", "agenda", "github", "docker", "ollama", "groq", "openrouter", "spotify", "mhnet"} <= ids
    assert "alertas" in client.get("/api/painel/alertas").json()
    assert client.get("/api/painel/nada").status_code == 404


# --------------------------------------------------------------------------- provedores de IA
def test_groq_e_openrouter_listam_so_modelos_com_ferramentas():
    groq = filter_models("groq", {"data": [{"id": "llama-3.3-70b-versatile"}, {"id": "whisper-large-v3"}]})
    assert [m["id"] for m in groq] == ["llama-3.3-70b-versatile"]
    orr = filter_models("openrouter", {"data": [{"id": "a/chat", "name": "Chat", "supported_parameters": ["tools"]},
                                                {"id": "b/img", "supported_parameters": ["temperature"]}]})
    assert [m["id"] for m in orr] == ["openrouter/auto", "a/chat"]
    assert filter_models("ollama", {"models": [{"name": "qwen3.5:2b"}]}) == [{"id": "qwen3.5:2b", "label": "qwen3.5:2b"}]


def test_cerebro_ollama_usa_o_endereco_local(monkeypatch):
    from jarvis.brain.openai_brain import OpenAIBrain
    monkeypatch.setattr(cfg.settings, "ollama_model", "qwen3.5:2b")
    b = OpenAIBrain("ollama")
    assert b.url == "http://127.0.0.1:11434/v1/chat/completions" and b.model == "qwen3.5:2b"
    assert "authorization" not in b.http.headers


def test_groq_sem_chave_nao_inicia():
    from jarvis.brain import BrainError, create_brain
    with pytest.raises(BrainError):
        create_brain("groq")
