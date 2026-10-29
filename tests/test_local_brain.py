"""O cérebro offline deve transformar frases em português nas ferramentas certas."""
import pytest

from jarvis.brain.local import LocalBrain, normalize


class FakeExecutor:
    """Registra as chamadas sem executar nada no PC."""

    def __init__(self):
        self.calls = []

    EXEMPLOS = {
        "buscar_web": {"ok": True, "resultados": [{"titulo": "Placar", "url": "https://x", "resumo": "Brasil 2 x 1"}]},
        "previsao_tempo": {"ok": True, "local": "São Paulo, SP",
                           "agora": {"temperatura_c": 17.3, "condicao": "nublado"},
                           "dias": [{"min_c": 14, "max_c": 22, "chance_chuva_pct": 70, "condicao": "chuva"},
                                    {"min_c": 15, "max_c": 25, "chance_chuva_pct": 10, "condicao": "céu limpo"}]},
        "teste_velocidade": {"ok": True, "download_mbps": 148.5, "upload_mbps": 40.2, "latencia_ms": 12},
    }

    def run(self, name, args):
        self.calls.append((name, args))
        return self.EXEMPLOS.get(name, {"ok": True, "mensagem": "ok"})


@pytest.fixture
def brain():
    return LocalBrain()


def ask(brain, text):
    ex = FakeExecutor()
    reply = brain.respond([{"role": "user", "text": text}], ex)
    return reply, ex.calls


@pytest.mark.parametrize("frase, ferramenta, args", [
    ("Jarvis, abra o Spotify", "abrir_aplicativo", {"nome": "Spotify"}),
    ("abre a calculadora", "abrir_aplicativo", {"nome": "calculadora"}),
    ("abra github.com", "abrir_site", {"url": "github.com"}),
    ("toque Back in Black no Spotify", "spotify_tocar", {"consulta": "Back in Black", "tipo": "musica"}),
    ("coloca a playlist Rock Clássico", "spotify_tocar", {"consulta": "Rock Clássico", "tipo": "playlist"}),
    ("pausa a música", "spotify_controle", {"acao": "pausar"}),
    ("próxima", "spotify_controle", {"acao": "proxima"}),
    ("volume em 30", "definir_volume", {"percentual": 30}),
    ("aumenta o volume", "controle_midia", {"acao": "volume_mais"}),
    ("encerre o processo chrome", "encerrar_processo", {"alvo": "chrome"}),
    ("pesquise receita de bolo no youtube", "pesquisar_web", {"consulta": "receita de bolo", "site": "youtube"}),
    ("pesquise quem ganhou o jogo do Brasil", "buscar_web", {"consulta": "quem ganhou o jogo do Brasil", "limite": 3}),
    ("qual a previsão do tempo para hoje?", "previsao_tempo", {"cidade": "", "dias": 5}),
    ("vai chover em São Paulo amanhã?", "previsao_tempo", {"cidade": "São Paulo", "dias": 5}),
    ("previsão do tempo de amanhã em Campinas", "previsao_tempo", {"cidade": "Campinas", "dias": 5}),
    ("faz um teste de velocidade da minha conexão", "teste_velocidade", {}),
    ("anote que a prova é sexta", "salvar_nota", {"texto": "a prova é sexta"}),
    ("gere um relatório", "gerar_relatorio", {}),
    ("desligue o computador em 5 minutos", "agendar_desligamento", {"acao": "desligar", "minutos": 5}),
    ("bloqueie a tela", "bloquear_tela", {}),
])
def test_intencoes(brain, frase, ferramenta, args):
    _, calls = ask(brain, frase)
    assert calls, f"nenhuma ferramenta chamada para: {frase}"
    assert calls[0] == (ferramenta, args)


def test_clima_responde_e_sugere_guarda_chuva(brain):
    reply, _ = ask(brain, "vai chover hoje?")
    assert "17 graus" in reply and "guarda-chuva" in reply


def test_pesquisa_responde_com_resultado(brain):
    reply, _ = ask(brain, "pesquise o placar do jogo")
    assert "Brasil 2 x 1" in reply


def test_consulta_de_memoria_sem_ferramenta(brain):
    reply, _ = ask(brain, "quanto de memória RAM está em uso?")
    assert "Memória em" in reply


def test_saudacao(brain):
    reply, calls = ask(brain, "bom dia jarvis")
    assert not calls and ("senhor" in reply or "Tony" in reply)


def test_chama_pelo_nome_as_vezes_mas_nao_sempre(brain):
    respostas = [ask(brain, "que horas são?")[0] for _ in range(200)]
    pelo_nome = sum("Tony" in r for r in respostas)
    assert 20 < pelo_nome < 120          # ~30% das vezes pelo nome, o resto "senhor"


def test_prompt_menciona_o_nome():
    from jarvis.brain.base import system_prompt
    assert "se chama Tony" in system_prompt() and "de vez em quando" in system_prompt()


def test_frase_desconhecida(brain):
    reply, calls = ask(brain, "qual o sentido da vida?")
    assert not calls and "offline" in reply


def test_normalize_preserva_tamanho():
    s = "Próxima música, AÇÃO!"
    assert len(normalize(s)) == len(s)
    assert normalize(s) == "proxima musica, acao!"
