"""Pesquisa/clima (HTML e JSON simulados) e configuração do Spotify pela interface, sem rede."""
import httpx
from fastapi.testclient import TestClient

import jarvis.config as cfg
from jarvis import spotify, web_tools
from jarvis.server import app

client = TestClient(app)

DDG_HTML = """
<div class="result results_links results_links_deep result--ad"><a class="result__a" href="https://anuncio">Anúncio</a></div>
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fpt.wikipedia.org%2Fwiki%2FCopa&amp;rut=x">Copa do <b>Mundo</b></a>
  <a class="result__snippet" href="#">A Copa do Mundo &amp; seus campeões.</a>
</div>
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a" href="https://ge.globo.com/copa">GE Copa</a>
</div>
"""


def test_busca_le_resultados_e_ignora_anuncios(monkeypatch):
    monkeypatch.setattr(web_tools.httpx, "post", lambda *a, **k: httpx.Response(200, text=DDG_HTML, request=httpx.Request("POST", "https://x")))
    r = web_tools.buscar_web("copa do mundo", 5)
    assert r["ok"] and r["fonte"] == "DuckDuckGo"
    assert r["resultados"][0] == {"titulo": "Copa do Mundo", "url": "https://pt.wikipedia.org/wiki/Copa",
                                  "resumo": "A Copa do Mundo & seus campeões."}
    assert [x["url"] for x in r["resultados"]] == ["https://pt.wikipedia.org/wiki/Copa", "https://ge.globo.com/copa"]


def test_previsao_traduz_codigo_do_tempo(monkeypatch):
    def fake_get(url, **kw):
        if "geocoding" in url:
            data = {"results": [{"name": "Paris", "country_code": "FR", "latitude": 1, "longitude": 1},
                                {"name": "Campinas", "admin1": "São Paulo", "country_code": "BR", "latitude": -22.9, "longitude": -47.1}]}
        else:
            data = {"current": {"temperature_2m": 20, "apparent_temperature": 19, "relative_humidity_2m": 60,
                                "weather_code": 63, "wind_speed_10m": 10, "precipitation": 1.2},
                    "daily": {"time": ["2026-10-26"], "weather_code": [3], "temperature_2m_max": [24],
                              "temperature_2m_min": [15], "precipitation_probability_max": [80], "precipitation_sum": [5]}}
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(web_tools.httpx, "get", fake_get)
    r = web_tools.previsao_tempo("Campinas", 1)
    assert r["local"] == "Campinas, São Paulo"          # prefere a cidade do Brasil
    assert r["agora"]["condicao"] == "chuva" and r["dias"][0]["condicao"] == "nublado"


def test_spotify_formato_invalido_recusado():
    r = client.post("/api/spotify", json={"client_id": "abc", "client_secret": "def"})
    assert r.status_code == 400 and "32 caracteres" in r.json()["detail"]


def test_spotify_salva_credenciais_validadas(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(spotify, "validate_credentials", lambda i, s: (True, "ok"))
    monkeypatch.setattr(cfg.settings, "spotify_client_id", "")
    monkeypatch.setattr(cfg.settings, "spotify_client_secret", "")
    r = client.post("/api/spotify", json={"client_id": "a" * 32, "client_secret": "b" * 32})
    assert r.status_code == 200
    d = r.json()
    assert d["configurado"] and not d["conectado"]       # falta o login da conta
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "SPOTIPY_CLIENT_ID=" + "a" * 32 in env and "SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback" in env


def test_spotify_recusa_credenciais_erradas(monkeypatch):
    monkeypatch.setattr(spotify, "validate_credentials", lambda i, s: (False, "O Spotify recusou o Client ID/Secret."))
    r = client.post("/api/spotify", json={"client_id": "a" * 32, "client_secret": "b" * 32})
    assert r.status_code == 400 and "recusou" in r.json()["detail"]


class FakeSpotify:
    """Simula a conta testada: a API não inicia músicas, mas fila, próxima e pausa funcionam."""

    def __init__(self, tocando=None):
        self.tocando, self.fila, self.chamadas = tocando, [], []

    def current_playback(self):
        return {"is_playing": True, "item": {"uri": self.tocando, "name": "x"}} if self.tocando else None

    def add_to_queue(self, uri):
        self.chamadas.append("fila")
        self.fila.append(uri)

    def next_track(self):
        self.chamadas.append("proxima")
        if self.fila:
            self.tocando = self.fila.pop(0)

    def pause_playback(self):
        self.chamadas.append("pausa")


def test_tocar_com_algo_tocando_usa_fila_e_proxima(monkeypatch):
    sp = FakeSpotify(tocando="spotify:track:velha")
    monkeypatch.setattr(spotify.time, "sleep", lambda s: None)
    monkeypatch.setattr(spotify, "_wake_desktop_app", lambda uri="": (_ for _ in ()).throw(AssertionError("não deveria abrir o app")))
    assert spotify._play_track(sp, "spotify:track:nova")
    assert sp.chamadas == ["fila", "proxima"]


def test_reaproveita_guia_do_web_player_ja_aberta(monkeypatch):
    """Spotify Web aberto mas parado: ativa a guia existente em vez de abrir outra."""
    sp = FakeSpotify(tocando=None)
    sp.devices = lambda: {"devices": [{"id": "celular", "name": "iPhone", "type": "Smartphone", "is_active": False},
                                      {"id": "web", "name": "Web Player (Microsoft Edge)", "type": "Computer",
                                       "is_active": False}]}

    def transfer(device_id, force_play=False):
        sp.chamadas.append(f"transferir:{device_id}")
        sp.tocando = "spotify:track:anterior"           # a guia retoma o que tinha

    sp.transfer_playback = transfer
    monkeypatch.setattr(spotify.time, "sleep", lambda s: None)
    monkeypatch.setattr(spotify, "_wake_desktop_app", lambda uri="": (_ for _ in ()).throw(AssertionError("abriu outra guia")))
    assert spotify._play_track(sp, "spotify:track:nova")
    assert sp.chamadas[:3] == ["transferir:web", "fila", "proxima"]


def test_tocar_com_spotify_parado_abre_no_app_do_pc(monkeypatch):
    sp = FakeSpotify(tocando=None)
    abertos = []

    def abre(uri=""):
        abertos.append(uri)
        sp.tocando = uri              # o app do PC começa a tocar a faixa aberta

    monkeypatch.setattr(spotify.time, "sleep", lambda s: None)
    monkeypatch.setattr(spotify, "_wake_desktop_app", abre)
    assert spotify._play_track(sp, "spotify:track:nova")
    assert abertos == ["spotify:track:nova"] and "fila" not in sp.chamadas


def test_pc_sem_app_do_spotify_usa_web_player(monkeypatch):
    """O caso do PC do trabalho: sem app registrado o link spotify: não dá erro, então nem tentamos."""
    from jarvis import os_control
    sp = FakeSpotify(tocando=None)
    abertos = []
    monkeypatch.setattr(spotify.time, "sleep", lambda s: None)
    monkeypatch.setattr(spotify, "_wait_playing", lambda *a: False)       # navegador espera o 1º clique
    monkeypatch.setattr(os_control, "protocol_registered", lambda scheme: False)
    monkeypatch.setattr(os_control, "_launch_target", lambda t: (_ for _ in ()).throw(AssertionError("não deveria abrir")))
    monkeypatch.setattr(spotify.webbrowser, "open", abertos.append)
    assert spotify._play_track(sp, "spotify:track:abc123") == "web"
    assert abertos == ["https://open.spotify.com/track/abc123"]


def test_registro_sobrando_mas_app_nao_abre_vai_para_web(monkeypatch):
    from jarvis import os_control
    abertos = []
    monkeypatch.setattr(spotify.time, "sleep", lambda s: None)
    monkeypatch.setattr(os_control, "protocol_registered", lambda scheme: True)
    monkeypatch.setattr(os_control, "_launch_target", lambda t: None)       # Windows "abre" sem erro...
    monkeypatch.setattr(spotify, "_spotify_process_running", lambda: False)  # ...mas nenhum Spotify aparece
    monkeypatch.setattr(spotify.webbrowser, "open", abertos.append)
    assert spotify._wake_desktop_app("spotify:track:xyz") == "web"
    assert abertos == ["https://open.spotify.com/track/xyz"]


def test_abrir_app_nao_instalado_abre_versao_web(monkeypatch):
    from jarvis import os_control
    abertos = []
    monkeypatch.setattr(os_control, "protocol_registered", lambda scheme: False)
    monkeypatch.setattr(os_control, "_launch_target", lambda t: (_ for _ in ()).throw(AssertionError("não deveria abrir")))
    monkeypatch.setattr(os_control, "_start_menu_shortcuts", lambda: [])
    monkeypatch.setattr(os_control, "_apps_menu_iniciar", lambda: [])
    monkeypatch.setattr(os_control, "_user_exe", lambda key: None)
    monkeypatch.setattr(os_control.shutil, "which", lambda *a: None)
    monkeypatch.setattr(os_control.webbrowser, "open", abertos.append)
    r = os_control.open_app("spotify")
    assert r["ok"] and abertos == ["https://open.spotify.com"] and "versão web" in r["mensagem"]


def test_card_sem_spotify_conectado_e_acao_invalida(monkeypatch):
    monkeypatch.setattr(cfg.settings, "spotify_client_id", "")
    assert client.get("/api/spotify/player").json() == {"conectado": False}
    assert client.post("/api/spotify/controle", json={"acao": "explodir"}).status_code == 400


def test_sem_login_nao_abre_navegador_no_meio_de_um_comando(monkeypatch):
    monkeypatch.setattr(cfg.settings, "spotify_client_id", "a" * 32)
    monkeypatch.setattr(cfg.settings, "spotify_client_secret", "b" * 32)
    monkeypatch.setattr(spotify, "_client", None)
    spotify._token_path().unlink(missing_ok=True)
    assert spotify._get_client() is None and "não conectada" in spotify._client_error


def test_apps_da_store_antes_do_terminal(monkeypatch):
    """"Abre o Claude" abre o app do PC (Microsoft Store), não o comando 'claude' do terminal."""
    from jarvis import os_control
    abertos = []
    monkeypatch.setattr(os_control, "IS_WINDOWS", True)
    monkeypatch.setattr(os_control, "_start_menu_shortcuts", lambda: [])
    monkeypatch.setattr(os_control, "_work_app", lambda key: None)
    monkeypatch.setattr(os_control, "_apps_menu_iniciar", lambda: [("Claude", "Claude_x!Claude"), ("ChatGPT", "OpenAI.Codex_y!App"),
                                                                 ("Desinstalar Claude", "z")])
    monkeypatch.setattr(os_control.shutil, "which", lambda *a: "C:/npm/claude.cmd")
    monkeypatch.setattr(os_control, "_spawn", lambda cmd, shell=False: abertos.append(cmd))
    assert os_control.open_app("claude")["alvo"] == "Claude_x!Claude"
    assert os_control.open_app("codex")["alvo"] == "OpenAI.Codex_y!App"
    assert abertos == [["explorer.exe", "shell:AppsFolder\Claude_x!Claude"], ["explorer.exe", "shell:AppsFolder\OpenAI.Codex_y!App"]]
