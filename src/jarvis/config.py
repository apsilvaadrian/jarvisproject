"""Configuração lida de variáveis de ambiente (e do arquivo .env, se existir).

Conceito de SO: variáveis de ambiente são o mecanismo padrão para passar
configuração a um processo sem alterar o código (chaves de API, porta, etc.).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
# Empacotado como .exe (PyInstaller): o programa roda de uma pasta temporária, então tudo que é
# do usuário (.env com chaves, notas, logs, relatórios) fica em %APPDATA%\JARVIS
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    USER_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "JARVIS"
    USER_DIR.mkdir(parents=True, exist_ok=True)
else:
    USER_DIR = ROOT_DIR


def _load_dotenv(path: Path) -> None:
    """Carrega KEY=VALUE do .env para os.environ sem sobrescrever o que já existe."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(USER_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _account_name() -> str:
    """Nome do usuário do sistema, só se parecer um nome de pessoa (ex.: 'Adrian', não 'user01')."""
    import re
    nome = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if re.fullmatch(r"[A-Za-zÀ-ÿ]{2,20}", nome) and nome.lower() not in {"user", "usuario", "admin", "administrator",
                                                                          "administrador", "root", "pc", "dono"}:
        return nome.capitalize()
    return ""


@dataclass
class Settings:
    host: str = field(default_factory=lambda: _env("JARVIS_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("JARVIS_PORT", "8000")))
    # Cérebro padrão: claude | openai | gemini | local
    brain: str = field(default_factory=lambda: _env("JARVIS_BRAIN", "auto").lower())
    tratamento: str = field(default_factory=lambda: _env("JARVIS_TRATAMENTO", "senhor"))
    # Nome do usuário (vazio = nome da conta do Windows/Linux, se parecer um nome)
    nome: str = field(default_factory=lambda: _env("JARVIS_NOME") or _account_name())
    # Grupo do WhatsApp para onde vai o status report (lembrado depois do primeiro envio)
    report_grupo: str = field(default_factory=lambda: _env("STATUS_REPORT_GRUPO"))
    # WhatsApp: auto (app se instalado, senão Web) | app | web
    whatsapp_modo: str = field(default_factory=lambda: _env("WHATSAPP_MODO", "auto").lower())
    # Navegador da janela do JARVIS e do WhatsApp Web: auto (Chrome, senão Edge) | chrome | edge
    navegador: str = field(default_factory=lambda: _env("JARVIS_NAVEGADOR", "auto").lower())
    # Como a janela abre: cheia (tela cheia) | maximizada | janela
    janela: str = field(default_factory=lambda: _env("JARVIS_JANELA", "maximizada").lower())
    # Janelinha flutuante quando a janela está minimizada/na bandeja: 1 (ligada) | 0
    flutuante: str = field(default_factory=lambda: _env("JARVIS_FLUTUANTE", "1"))
    # Apps da web já usados dentro do JARVIS (reabrem escondidos ao iniciar, para os avisos): "whatsapp,instagram"
    apps_web: str = field(default_factory=lambda: _env("JARVIS_APPS_WEB"))
    # Visual do JARVIS dentro dos apps (modo escuro + cores da interface): 1 | 0
    apps_tema: str = field(default_factory=lambda: _env("JARVIS_APPS_TEMA", "1"))
    # Cidade padrão para a previsão do tempo (vazio = localização aproximada pelo IP)
    cidade: str = field(default_factory=lambda: _env("JARVIS_CIDADE"))
    data_dir: Path = field(default_factory=lambda: Path(_env("JARVIS_DATA_DIR", str(USER_DIR / "data"))))
    log_dir: Path = field(default_factory=lambda: Path(_env("JARVIS_LOG_DIR", str(USER_DIR / "logs"))))
    report_dir: Path = field(default_factory=lambda: Path(_env("JARVIS_REPORT_DIR", str(USER_DIR / "reports"))))

    anthropic_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    openai_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-6-luna"))
    # Permite usar qualquer API compatível com OpenAI (Ollama, LM Studio, Groq...)
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    gemini_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-3.1-flash-lite"))
    # Nível de "pensamento" dos modelos Gemini 3+: minimal | low | high ("" = padrão do modelo)
    gemini_thinking: str = field(default_factory=lambda: _env("GEMINI_THINKING", "minimal"))

    # Provedores compatíveis com a API da OpenAI
    groq_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "llama-3.3-70b-versatile"))
    openrouter_key: str = field(default_factory=lambda: _env("OPENROUTER_API_KEY"))
    openrouter_model: str = field(default_factory=lambda: _env("OPENROUTER_MODEL", "openrouter/auto"))
    # Ollama (modelos locais); modelo vazio = o primeiro instalado
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL"))

    # Integrações (todas opcionais)
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    agenda_ics: str = field(default_factory=lambda: _env("AGENDA_ICS"))           # endereço secreto iCal
    email_imap: str = field(default_factory=lambda: _env("EMAIL_IMAP", "imap.gmail.com"))
    email_usuario: str = field(default_factory=lambda: _env("EMAIL_USUARIO"))
    email_senha: str = field(default_factory=lambda: _env("EMAIL_SENHA_APP"))    # senha de app, nunca a normal

    # Contas pessoais via CLIs oficiais (vazio = modelo padrão do seu plano)
    claude_conta_model: str = field(default_factory=lambda: _env("CLAUDE_CONTA_MODEL"))
    openai_conta_model: str = field(default_factory=lambda: _env("OPENAI_CONTA_MODEL"))
    gemini_conta_model: str = field(default_factory=lambda: _env("GEMINI_CONTA_MODEL"))

    # Servidor de voz clonada (voz/servidor_voz.py), opcional
    voz_url: str = field(default_factory=lambda: _env("JARVIS_VOZ_URL", "http://127.0.0.1:8001"))
    # ElevenLabs (voz clonada na nuvem), opcional
    elevenlabs_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_voice_id: str = field(default_factory=lambda: _env("ELEVENLABS_VOICE_ID"))
    elevenlabs_model: str = field(default_factory=lambda: _env("ELEVENLABS_MODEL", "eleven_flash_v2_5"))

    spotify_client_id: str = field(default_factory=lambda: _env("SPOTIPY_CLIENT_ID"))
    spotify_client_secret: str = field(default_factory=lambda: _env("SPOTIPY_CLIENT_SECRET"))
    spotify_redirect_uri: str = field(
        default_factory=lambda: _env("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    )

    def available_brains(self) -> list[str]:
        brains = []
        if self.anthropic_key:
            brains.append("claude")
        if self.openai_key or "api.openai.com" not in self.openai_base_url:
            brains.append("openai")
        if self.gemini_key:
            brains.append("gemini")
        if self.groq_key:
            brains.append("groq")
        if self.openrouter_key:
            brains.append("openrouter")
        if ollama_models():
            brains.append("ollama")
        from . import contas  # import tardio: contas não depende da configuração
        brains += [c for c in contas.CONTAS if contas.instalada(c)]
        brains.append("local")
        return brains

    def default_brain(self) -> str:
        available = self.available_brains()
        if self.brain in available:
            return self.brain
        return available[0]

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.log_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)

    def brain_status(self) -> list[dict[str, object]]:
        """Todos os cérebros, indicando quais já têm chave configurada."""
        from . import contas

        available = self.available_brains()
        out = []
        for brain, (_, _, attr_model) in BRAIN_ENV.items():
            out.append({"id": brain, "tipo": "api", "configured": brain in available, "model": getattr(self, attr_model)})
        out.append({"id": "ollama", "tipo": "local", "configured": "ollama" in available,
                    "model": self.ollama_model or (ollama_models() or ["nenhum modelo instalado"])[0]})
        for conta_id in contas.CONTAS:
            # "configured" aqui = CLI instalada; o login é conferido em /api/contas
            out.append({"id": conta_id, "tipo": "conta", "configured": conta_id in available,
                        "model": getattr(self, conta_id.split("-")[0] + "_conta_model") or "padrão do plano"})
        out.append({"id": "local", "tipo": "local", "configured": True, "model": "regras offline"})
        return out

    def brain_key(self, brain: str) -> str:
        return getattr(self, KEY_ATTR[brain])

    def set_brain_key(self, brain: str, key: str, model: str = "") -> None:
        """Atualiza a chave (e o modelo) em memória e persiste no arquivo .env."""
        key_var, model_var, attr_model = BRAIN_ENV[brain]
        updates = {key_var: key}
        if model:
            updates[model_var] = model
        for var, value in updates.items():
            os.environ[var] = value
        setattr(self, KEY_ATTR[brain], key)
        if model:
            setattr(self, attr_model, model)
        update_env_file(ENV_FILE, updates)

    def set_elevenlabs(self, key: str = "", voice_id: str = "", model: str = "") -> None:
        """Atualiza só os campos informados (em memória e no .env)."""
        updates = {}
        for var, attr, value in (("ELEVENLABS_API_KEY", "elevenlabs_key", key),
                                 ("ELEVENLABS_VOICE_ID", "elevenlabs_voice_id", voice_id),
                                 ("ELEVENLABS_MODEL", "elevenlabs_model", model)):
            if value:
                updates[var] = value
                os.environ[var] = value
                setattr(self, attr, value)
        if updates:
            update_env_file(ENV_FILE, updates)

    def set_perfil(self, nome: str, tratamento: str) -> None:
        self.nome, self.tratamento = nome, tratamento or "senhor"
        os.environ["JARVIS_NOME"], os.environ["JARVIS_TRATAMENTO"] = self.nome, self.tratamento
        update_env_file(ENV_FILE, {"JARVIS_NOME": self.nome, "JARVIS_TRATAMENTO": self.tratamento})

    def set_spotify(self, client_id: str, client_secret: str) -> None:
        for var, attr, value in (("SPOTIPY_CLIENT_ID", "spotify_client_id", client_id),
                                 ("SPOTIPY_CLIENT_SECRET", "spotify_client_secret", client_secret)):
            os.environ[var] = value
            setattr(self, attr, value)
        update_env_file(ENV_FILE, {"SPOTIPY_CLIENT_ID": client_id, "SPOTIPY_CLIENT_SECRET": client_secret,
                                   "SPOTIPY_REDIRECT_URI": self.spotify_redirect_uri})

    def set_vars(self, updates: dict[str, str]) -> None:
        """Grava variáveis da tabela VAR_ATTR (em memória, no ambiente e no .env)."""
        for var, value in updates.items():
            os.environ[var] = value
            setattr(self, VAR_ATTR[var], value)
        update_env_file(ENV_FILE, updates)

    def set_brain_model(self, brain: str, model: str) -> None:
        """Troca só o modelo; vale já na próxima pergunta (os cérebros leem o modelo a cada chamada)."""
        if brain == "ollama":
            self.set_vars({"OLLAMA_MODEL": model})
            return
        _, model_var, attr_model = BRAIN_ENV[brain]
        os.environ[model_var] = model
        setattr(self, attr_model, model)
        update_env_file(ENV_FILE, {model_var: model})


# cérebro -> (variável da chave, variável do modelo, atributo do modelo em Settings)
BRAIN_ENV = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "anthropic_model"),
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "openai_model"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini_model"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "groq_model"),
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "openrouter_model"),
}
KEY_ATTR = {"claude": "anthropic_key", "openai": "openai_key", "gemini": "gemini_key", "groq": "groq_key",
            "openrouter": "openrouter_key"}
# Variáveis que a interface pode gravar (integrações, Ollama) -> atributo em Settings
VAR_ATTR = {"GITHUB_TOKEN": "github_token", "AGENDA_ICS": "agenda_ics", "EMAIL_IMAP": "email_imap",
            "EMAIL_USUARIO": "email_usuario", "EMAIL_SENHA_APP": "email_senha", "OLLAMA_URL": "ollama_url",
            "OLLAMA_MODEL": "ollama_model", "JARVIS_JANELA": "janela", "JARVIS_BRAIN": "brain",
            "WHATSAPP_MODO": "whatsapp_modo", "JARVIS_NAVEGADOR": "navegador",
            "STATUS_REPORT_GRUPO": "report_grupo", "JARVIS_FLUTUANTE": "flutuante", "JARVIS_APPS_WEB": "apps_web",
            "JARVIS_APPS_TEMA": "apps_tema"}
_ollama_cache: dict[str, object] = {"em": 0.0, "modelos": []}


def ollama_models(max_idade: float = 30.0) -> list[str]:
    """Modelos instalados no Ollama deste PC ([] se o Ollama não estiver rodando). Cache de 30 s."""
    import time
    agora = time.monotonic()
    if agora - float(_ollama_cache["em"]) < max_idade and _ollama_cache["em"]:
        return list(_ollama_cache["modelos"])  # type: ignore[arg-type]
    modelos: list[str] = []
    if os.environ.get("JARVIS_SEM_OLLAMA") != "1":
        try:
            import httpx
            r = httpx.get(settings.ollama_url.rstrip("/") + "/api/tags", timeout=0.6)
            modelos = [m["name"] for m in r.json().get("models", [])] if r.status_code == 200 else []
        except Exception:
            modelos = []
    _ollama_cache.update(em=agora, modelos=modelos)
    return modelos
ENV_FILE = USER_DIR / ".env"


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Substitui (ou acrescenta) linhas KEY=VALUE no .env, preservando o resto do arquivo."""
    # Remove quebras de linha para que um valor nunca crie outras variáveis
    updates = {k: v.replace("\r", "").replace("\n", "").strip() for k, v in updates.items()}
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pending = dict(updates)
    for i, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if name in pending and not line.lstrip().startswith("#"):
            lines[i] = f"{name}={pending.pop(name)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)  # contém segredos: só o dono lê


settings = Settings()
