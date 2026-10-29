"""Isola os testes: diretórios temporários e nenhum cérebro online (sem chamadas de rede)."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="jarvis-test-")
os.environ["JARVIS_DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["JARVIS_LOG_DIR"] = os.path.join(_tmp, "logs")
os.environ["JARVIS_REPORT_DIR"] = os.path.join(_tmp, "reports")
os.environ["JARVIS_BRAIN"] = "local"
os.environ["JARVIS_NOME"] = "Tony"
os.environ["JARVIS_TRATAMENTO"] = "senhor"
# Valores vazios impedem que o .env real seja carregado (setdefault)
for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET",
            "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
    os.environ[key] = ""
# Sem MHnet Notifier de verdade nos testes (cada teste de massivas cria o seu banco)
os.environ["MHNET_DB"] = os.path.join(_tmp, "sem-notifier.db")
# Integrações e provedores novos também vazios; e nada de consultar o Ollama real
for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN", "AGENDA_ICS", "EMAIL_USUARIO", "EMAIL_SENHA_APP"):
    os.environ[key] = ""
os.environ["JARVIS_SEM_OLLAMA"] = "1"

# Nada de gravar no .env real durante os testes (ex.: trocar de cérebro agora fica salvo)
import jarvis.config as _cfg  # noqa: E402
_cfg.ENV_FILE = __import__("pathlib").Path(_tmp) / ".env"
