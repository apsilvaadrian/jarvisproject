"""Integrações opcionais: Google Agenda (iCal), Gmail/e-mail (IMAP), GitHub, Docker — e o resumo de serviços/alertas.

Tudo sem servidor de terceiros nem "login com Google":
- Agenda: o "endereço secreto no formato iCal" do Google Agenda (Configurações da agenda → Integrar
  agenda). Só leitura. Aceita vários endereços separados por espaço (agenda pessoal + trabalho).
- E-mail: IMAP com **senha de app** (Gmail: myaccount.google.com/apppasswords). Só leitura: o JARVIS
  conta e lista os não lidos com BODY.PEEK, sem marcar nada como lido.
- GitHub: token pessoal (github.com/settings/tokens) — notificações, PRs e repositórios.
- Docker: a CLI `docker` deste PC (listar; iniciar/parar/reiniciar pede confirmação).
"""
from __future__ import annotations

import email as email_lib
import imaplib
import json
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable

import httpx

from .config import ollama_models, settings
from .logger import log

_cache: dict[str, tuple[float, Any]] = {}


def _cacheado(chave: str, idade: float, fn: Callable[[], Any]) -> Any:
    agora = time.monotonic()
    if chave in _cache and agora - _cache[chave][0] < idade:
        return _cache[chave][1]
    valor = fn()
    _cache[chave] = (agora, valor)
    return valor


def limpar_cache(prefixo: str = "") -> None:
    for k in [k for k in _cache if k.startswith(prefixo)]:
        _cache.pop(k, None)


# --------------------------------------------------------------------------- Agenda (iCal)
def _ler_ics(url: str) -> bytes:
    url = url.strip().replace("webcal://", "https://", 1)
    r = httpx.get(url, timeout=15, follow_redirects=True)
    r.raise_for_status()
    if b"BEGIN:VCALENDAR" not in r.content[:2000]:
        raise ValueError("o endereço não devolveu uma agenda iCal (.ics)")
    return r.content


def eventos_do_ics(conteudo: bytes, inicio: datetime, fim: datetime) -> list[dict[str, Any]]:
    """Eventos (com repetições expandidas) entre inicio e fim. Função pura, testável."""
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(conteudo)
    nome_agenda = str(cal.get("X-WR-CALNAME", "") or "")
    out = []
    for ev in recurring_ical_events.of(cal).between(inicio, fim):
        ini, fim_ev = ev.get("DTSTART").dt, (ev.get("DTEND").dt if ev.get("DTEND") else None)
        dia_inteiro = isinstance(ini, date) and not isinstance(ini, datetime)
        if not dia_inteiro and ini.tzinfo is not None:
            ini = ini.astimezone().replace(tzinfo=None)
            fim_ev = fim_ev.astimezone().replace(tzinfo=None) if isinstance(fim_ev, datetime) and fim_ev.tzinfo else fim_ev
        out.append({"titulo": str(ev.get("SUMMARY", "(sem título)")), "local": str(ev.get("LOCATION", "") or ""),
                    "inicio": ini.isoformat(), "fim": fim_ev.isoformat() if fim_ev else None,
                    "dia_inteiro": dia_inteiro, "agenda": nome_agenda})
    return out


def agenda(dias: int = 1) -> dict[str, Any]:
    urls = settings.agenda_ics.split()
    if not urls:
        return {"ok": False, "conectado": False, "mensagem": "Agenda não conectada. Em Integrações, cole o "
                "endereço secreto iCal do Google Agenda."}
    dias = max(1, min(int(dias or 1), 30))
    hoje = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    def buscar() -> dict[str, Any]:
        eventos, erros = [], []
        for url in urls:
            try:
                eventos += eventos_do_ics(_ler_ics(url), hoje, hoje + timedelta(days=30))
            except Exception as exc:  # um endereço com problema não derruba os outros
                erros.append(str(exc)[:120])
        return {"eventos": eventos, "erros": erros}

    dados = _cacheado("agenda", 300, buscar)
    agora = datetime.now()
    limite = hoje + timedelta(days=dias)
    eventos = [e for e in dados["eventos"]
               if datetime.fromisoformat(e["inicio"][:19]) < limite
               and (e["dia_inteiro"] or datetime.fromisoformat((e["fim"] or e["inicio"])[:19]) >= agora)]
    eventos.sort(key=lambda e: (e["inicio"][:10], not e["dia_inteiro"], e["inicio"]))
    r: dict[str, Any] = {"ok": True, "conectado": True, "dias": dias, "eventos": eventos[:40]}
    if dados["erros"] and not dados["eventos"]:
        r.update(ok=False, mensagem="Não consegui ler a agenda: " + dados["erros"][0])
    return r


# --------------------------------------------------------------------------- E-mail (IMAP, só leitura)
def _decod(valor: str | None) -> str:
    try:
        return str(make_header(decode_header(valor or ""))).strip()
    except Exception:
        return (valor or "").strip()


def _imap() -> imaplib.IMAP4_SSL:
    con = imaplib.IMAP4_SSL(settings.email_imap, 993, timeout=15)
    con.login(settings.email_usuario, settings.email_senha)
    return con


def email(limite: int = 5) -> dict[str, Any]:
    if not (settings.email_usuario and settings.email_senha):
        return {"ok": False, "conectado": False, "mensagem": "E-mail não conectado. Em Integrações, informe o "
                "e-mail e uma senha de app (não a senha normal)."}
    limite = max(1, min(int(limite or 5), 15))

    def buscar() -> dict[str, Any]:
        con = _imap()
        try:
            con.select("INBOX", readonly=True)            # só leitura: nada é marcado como lido
            _, dados = con.search(None, "UNSEEN")
            ids = dados[0].split() if dados and dados[0] else []
            mensagens = []
            for mid in reversed(ids[-15:]):
                _, partes = con.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                bruto = next((p[1] for p in partes if isinstance(p, tuple)), b"")
                msg = email_lib.message_from_bytes(bruto)
                nome, endereco = parseaddr(_decod(msg.get("From")))
                try:
                    quando = parsedate_to_datetime(msg.get("Date")).astimezone().strftime("%d/%m %H:%M")
                except Exception:
                    quando = ""
                mensagens.append({"de": nome or endereco, "endereco": endereco,
                                  "assunto": _decod(msg.get("Subject")) or "(sem assunto)", "quando": quando})
            return {"nao_lidos": len(ids), "mensagens": mensagens}
        finally:
            try:
                con.logout()
            except Exception:
                pass

    try:
        dados = _cacheado("email", 60, buscar)
    except imaplib.IMAP4.error as exc:
        return {"ok": False, "conectado": True, "mensagem": f"O servidor recusou o login ({str(exc)[:100]}). "
                "Confira o e-mail e a senha de app."}
    except OSError as exc:
        return {"ok": False, "conectado": True, "mensagem": f"Sem conexão com {settings.email_imap} ({exc.__class__.__name__})."}
    return {"ok": True, "conectado": True, "conta": settings.email_usuario, "nao_lidos": dados["nao_lidos"],
            "mensagens": dados["mensagens"][:limite]}


# --------------------------------------------------------------------------- GitHub
def _gh(caminho: str, **params: Any) -> Any:
    r = httpx.get("https://api.github.com" + caminho, params=params, timeout=10,
                  headers={"authorization": f"Bearer {settings.github_token}", "accept": "application/vnd.github+json",
                           "x-github-api-version": "2022-11-28"})
    if r.status_code == 401:
        raise PermissionError("o GitHub recusou o token")
    r.raise_for_status()
    return r.json()


def github() -> dict[str, Any]:
    if not settings.github_token:
        return {"ok": False, "conectado": False, "mensagem": "GitHub não conectado. Em Integrações, cole um token pessoal."}

    def buscar() -> dict[str, Any]:
        user = _gh("/user")
        login = user["login"]
        notif = _gh("/notifications", per_page=50)
        meus = _gh("/search/issues", q=f"is:open is:pr author:{login}", per_page=5)
        revisar = _gh("/search/issues", q=f"is:open is:pr review-requested:{login}", per_page=5)
        repos = _gh("/user/repos", sort="pushed", per_page=5)
        pr = lambda i: {"titulo": i["title"], "repo": i["repository_url"].split("/repos/")[-1], "url": i["html_url"],  # noqa: E731
                        "numero": i["number"]}
        return {"usuario": login, "nome": user.get("name") or login, "notificacoes": len(notif),
                "meus_prs": [pr(i) for i in meus.get("items", [])], "revisar": [pr(i) for i in revisar.get("items", [])],
                "total_meus_prs": meus.get("total_count", 0), "total_revisar": revisar.get("total_count", 0),
                "repos": [{"nome": r["full_name"], "url": r["html_url"], "atualizado": r.get("pushed_at", "")[:10],
                           "privado": r.get("private", False)} for r in repos]}

    try:
        return {"ok": True, "conectado": True, **_cacheado("github", 90, buscar)}
    except PermissionError as exc:
        return {"ok": False, "conectado": True, "mensagem": f"{exc}; gere outro token."}
    except httpx.HTTPError as exc:
        return {"ok": False, "conectado": True, "mensagem": f"GitHub indisponível ({exc.__class__.__name__})."}


# --------------------------------------------------------------------------- Docker
def docker() -> dict[str, Any]:
    exe = shutil.which("docker")
    if not exe:
        return {"ok": False, "conectado": False, "mensagem": "Docker não está instalado neste PC."}
    try:
        r = subprocess.run([exe, "ps", "-a", "--format", "{{json .}}"], capture_output=True, text=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "conectado": True, "mensagem": "O Docker não respondeu (o Docker Desktop está aberto?)."}
    if r.returncode != 0:
        return {"ok": False, "conectado": True, "mensagem": "O Docker não está rodando: " + (r.stderr.strip()[:150] or "erro")}
    conts = []
    for linha in r.stdout.splitlines():
        try:
            c = json.loads(linha)
        except ValueError:
            continue
        conts.append({"nome": c.get("Names"), "imagem": c.get("Image"), "estado": c.get("State"),
                      "status": c.get("Status"), "portas": c.get("Ports", "")})
    return {"ok": True, "conectado": True, "rodando": sum(c["estado"] == "running" for c in conts), "containers": conts}


def docker_controlar(container: str, acao: str) -> dict[str, Any]:
    acoes = {"iniciar": "start", "parar": "stop", "reiniciar": "restart"}
    if acao not in acoes:
        return {"ok": False, "mensagem": "Ação inválida: iniciar, parar ou reiniciar."}
    exe = shutil.which("docker")
    if not exe:
        return {"ok": False, "mensagem": "Docker não está instalado neste PC."}
    r = subprocess.run([exe, acoes[acao], container], capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        return {"ok": False, "mensagem": f"O Docker recusou: {r.stderr.strip()[:200]}"}
    return {"ok": True, "mensagem": f"Container {container}: {acao} feito."}


# --------------------------------------------------------------------------- configuração e status
# id -> campos que a tela de Integrações pode gravar
CAMPOS = {
    "agenda": [{"var": "AGENDA_ICS", "rotulo": "Endereço secreto iCal", "tipo": "password",
                "dica": "Google Agenda → Configurações da agenda → Integrar agenda → Endereço secreto no formato iCal. "
                        "Vários? Separe por espaço."}],
    "email": [{"var": "EMAIL_USUARIO", "rotulo": "E-mail", "tipo": "text", "dica": "ex.: voce@gmail.com"},
              {"var": "EMAIL_SENHA_APP", "rotulo": "Senha de app", "tipo": "password",
               "dica": "Gmail: myaccount.google.com/apppasswords (precisa da verificação em 2 etapas). Não use a senha normal."},
              {"var": "EMAIL_IMAP", "rotulo": "Servidor IMAP", "tipo": "text",
               "dica": "Gmail: imap.gmail.com · Outlook: outlook.office365.com"}],
    "github": [{"var": "GITHUB_TOKEN", "rotulo": "Token pessoal", "tipo": "password",
                "dica": "github.com/settings/tokens → token clássico com notifications e repo (só leitura basta)."}],
    "ollama": [{"var": "OLLAMA_URL", "rotulo": "Endereço do Ollama", "tipo": "text", "dica": "Padrão: http://127.0.0.1:11434"}],
}


def configurar(iid: str, campos: dict[str, str]) -> dict[str, Any]:
    if iid not in CAMPOS:
        raise ValueError("Essa integração não é configurada aqui.")
    permitidos = {c["var"] for c in CAMPOS[iid]}
    novos = {k: v.strip() for k, v in campos.items() if k in permitidos and v and v.strip()}
    if not novos:
        raise ValueError("Preencha os campos.")
    antigos = {k: getattr(settings, _attr(k)) for k in novos}
    settings.set_vars(novos)
    limpar_cache(iid)
    teste = {"agenda": lambda: agenda(1), "email": lambda: email(1), "github": github,
             "ollama": lambda: {"ok": bool(ollama_models(0)), "mensagem": "O Ollama não respondeu nesse endereço."}}[iid]()
    if not teste.get("ok") and iid in ("email", "github") and "recus" in str(teste.get("mensagem", "")):
        settings.set_vars({k: v for k, v in antigos.items()})   # credencial recusada: volta ao que era
        limpar_cache(iid)
        raise ValueError(teste["mensagem"])
    log.info("Integração %s configurada pela interface (teste ok=%s)", iid, teste.get("ok"))
    return {"ok": bool(teste.get("ok")), "mensagem": "Conectado." if teste.get("ok") else teste.get("mensagem", "")}


def _attr(var: str) -> str:
    from .config import VAR_ATTR
    return VAR_ATTR[var]


def desconectar(iid: str) -> None:
    if iid not in CAMPOS or iid == "ollama":
        raise ValueError("Essa integração não é desconectada aqui.")
    settings.set_vars({c["var"]: "" for c in CAMPOS[iid] if c["var"] != "EMAIL_IMAP"})
    limpar_cache(iid)


def status() -> list[dict[str, Any]]:
    """Cartões da aba Integrações: o que existe, se está conectado e como configurar."""
    from . import massivas, spotify, trabalho
    modelos = ollama_models()
    itens = [
        ("claude", "Claude", "LLM", "Claude Sonnet, Opus e Haiku pela API.", bool(settings.anthropic_key), "ai_core"),
        ("openai", "OpenAI", "LLM", "GPT pela API.", bool(settings.openai_key), "ai_core"),
        ("gemini", "Gemini", "LLM", "Gemini pela API (plano grátis).", bool(settings.gemini_key), "ai_core"),
        ("groq", "Groq", "LLM", "Llama e outros modelos abertos, rápidos e com plano grátis.", bool(settings.groq_key), "ai_core"),
        ("openrouter", "OpenRouter", "LLM", "Mais de 200 modelos com uma chave só.", bool(settings.openrouter_key), "ai_core"),
        ("ollama", "Ollama", "LLM local", f"Modelos locais, privacidade total. {len(modelos)} instalado(s)."
         if modelos else "Modelos locais, privacidade total (instale em ollama.com).", bool(modelos), "campos"),
        ("email", "Gmail / E-mail", "Google", "Não lidos e remetentes por IMAP (só leitura).",
         bool(settings.email_usuario and settings.email_senha), "campos"),
        ("agenda", "Google Agenda", "Google", "Compromissos de hoje e da semana (endereço iCal).",
         bool(settings.agenda_ics), "campos"),
        ("github", "GitHub", "DevTool", "Notificações, pull requests e repositórios.", bool(settings.github_token), "campos"),
        ("docker", "Docker", "DevTool", "Containers: listar, iniciar, parar e reiniciar.", bool(shutil.which("docker")), "nenhum"),
        ("whatsapp", "WhatsApp", "Mensagens", "Envia mensagens pelo app do PC ou pelo WhatsApp Web, sempre "
         "com a sua confirmação.", True, "whatsapp"),
        ("spotify", "Spotify", "Música", "Tocar, controlar, letra sincronizada.", spotify.is_connected(), "configuracoes"),
        ("elevenlabs", "ElevenLabs", "Voz", "Sua voz clonada na nuvem.", bool(settings.elevenlabs_key), "ai_voice"),
        ("mhnet", "MHnet Notifier", "Trabalho", "Massivas sincronizadas (só leitura).", massivas.db_path().is_file(), "nenhum"),
        ("statusrobot", "StatusRobotReport", "Trabalho", "Status report preenchido pelo JARVIS.",
         trabalho.localizar() is not None, "nenhum"),
    ]
    return [{"id": i, "nome": n, "categoria": c, "descricao": d, "conectado": ok, "config": cfg,
             "campos": [{k: v for k, v in campo.items()} | {"preenchido": bool(getattr(settings, _attr(campo["var"])))}
                        for campo in CAMPOS.get(i, [])]}
            for i, n, c, d, ok, cfg in itens]


def servicos() -> dict[str, Any]:
    return {"servicos": [{"id": s["id"], "nome": s["nome"], "conectado": s["conectado"], "categoria": s["categoria"]}
                         for s in status() if s["categoria"] not in ("LLM",)]}


def alertas() -> dict[str, Any]:
    """Alertas importantes do momento (card Alertas e o sino do topo)."""
    from . import massivas
    from . import system_monitor as sm
    out: list[dict[str, Any]] = []
    m = massivas.consultar(incluir_avisos=True)
    if m.get("ok"):
        n = m["total_ativas_no_geral"]
        if n:
            out.append({"nivel": "warn", "titulo": f"{n} massiva(s) em aberto",
                        "detalhe": f"{m['total_afetados_encontrados']} clientes afetados", "pedido": "atualizações do trabalho"})
        for a in m.get("avisos_n2", [])[:3]:
            out.append({"nivel": "crit" if "alert" in str(a.get("prioridade", "")).lower() else "warn",
                        "titulo": a.get("titulo") or "Aviso N2", "detalhe": f"Aviso N2 · {a.get('prioridade') or ''}"})
        if m.get("aviso"):
            out.append({"nivel": "warn", "titulo": "MHnet Notifier sem sincronizar", "detalhe": m["aviso"][:90],
                        "pedido": "abra o MHnet Notifier"})
    if settings.email_usuario and settings.email_senha:
        e = email(3)
        if e.get("ok") and e["nao_lidos"]:
            out.append({"nivel": "info", "titulo": f"{e['nao_lidos']} e-mail(s) não lido(s)",
                        "detalhe": f"Último: {e['mensagens'][0]['assunto'][:60]}" if e["mensagens"] else "",
                        "pedido": "quais e-mails não lidos eu tenho?"})
    if settings.agenda_ics:
        a = agenda(1)
        agora = datetime.now()
        for ev in a.get("eventos", []):
            if ev["dia_inteiro"]:
                continue
            falta = (datetime.fromisoformat(ev["inicio"][:19]) - agora).total_seconds() / 60
            if 0 <= falta <= 60:
                out.append({"nivel": "info", "titulo": f"{ev['titulo']} em {int(falta)} min",
                            "detalhe": ev["local"] or "Agenda", "pedido": "qual a minha agenda de hoje?"})
    snap = sm.snapshot()
    for d in snap.get("discos", []):
        if d["percent"] >= 90:
            out.append({"nivel": "crit", "titulo": f"Disco {d['ponto_montagem']} {d['percent']:.0f}% cheio",
                        "detalhe": f"{d['livre_gb']:.0f} GB livres", "pedido": f"analise a pasta {d['ponto_montagem']}"})
    if snap["memoria"]["percent"] >= 90:
        out.append({"nivel": "crit", "titulo": f"Memória em {snap['memoria']['percent']:.0f}%",
                    "detalhe": "Veja o que está consumindo", "pedido": "quais processos estão consumindo mais memória?"})
    t = snap.get("temperatura", {}).get("cpu_c")
    if t and t >= 85:
        out.append({"nivel": "crit", "titulo": f"CPU a {t:.0f} °C", "detalhe": "Temperatura alta"})
    return {"alertas": out}
