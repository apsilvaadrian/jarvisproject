"""WhatsApp: enviar mensagens pelo app do PC ou pelo WhatsApp Web (sempre com a sua confirmação antes).

- Contatos: o JARVIS guarda nome -> número em data/contatos.json ("salva o contato Maria 42 99999-0000").
  Também aceita o número direto no pedido.
- Preferido: a EXTENSÃO do JARVIS no seu Chrome/Edge (pasta extensao_whatsapp). Ela usa o WhatsApp Web que
  já está aberto e logado no seu navegador: pesquisa, confere a conversa, digita, envia e confere, dentro da
  aba, sem roubar o foco. Conversa com o JARVIS por WebSocket em 127.0.0.1 (/ws/whatsapp).
- Reserva (sem a extensão): WhatsApp Web controlado pelo JARVIS, em qualquer PC. Abre web.whatsapp.com numa janela própria do
  Chrome (ou Edge, se não houver Chrome ou a empresa bloquear), fora da tela, com perfil próprio
  (%APPDATA%\\JARVIS\\whatsapp-web-chrome ou whatsapp-web — QR code uma vez por PC e navegador), e
  controla a página pelo protocolo de depuração do navegador (CDP, só em 127.0.0.1 e só durante o envio).
  Pesquisa o NOME dentro do seu WhatsApp (contatos do celular e grupos), confere o cabeçalho da conversa antes
  de digitar e confere que a mensagem apareceu como enviada.
- Modo "app" (opcional): o WhatsApp Desktop não deixa o JARVIS ler a tela (é uma página embutida fechada), então
  nele só dá para mandar para números, pelo link whatsapp://send, sem conferir.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import unicodedata
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from .config import USER_DIR, settings
from .logger import log

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- contatos
def _arquivo() -> Path:
    return settings.data_dir / "contatos.json"


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def normalizar_numero(texto: str) -> str | None:
    """'(42) 99999-0000' -> '5542999990000'. Sem DDI, assume Brasil (55)."""
    d = re.sub(r"\D", "", texto or "")
    if len(d) in (10, 11):
        d = "55" + d
    return d if 12 <= len(d) <= 15 else None


def formatar(numero: str) -> str:
    if numero.startswith("55") and len(numero) in (12, 13):
        n = numero[4:]
        return f"+55 {numero[2:4]} {n[:-4]}-{n[-4:]}"
    return "+" + numero


def contatos() -> list[dict[str, str]]:
    try:
        return json.loads(_arquivo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _gravar(lista: list[dict[str, str]]) -> None:
    settings.ensure_dirs()
    _arquivo().write_text(json.dumps(sorted(lista, key=lambda c: _norm(c["nome"])), ensure_ascii=False, indent=1),
                          encoding="utf-8")


def salvar_contato(nome: str, numero: str) -> dict[str, Any]:
    nome = " ".join((nome or "").split()).strip(" .,:")
    num = normalizar_numero(numero)
    if not nome or not num:
        return {"ok": False, "mensagem": "Preciso do nome e de um número com DDD, ex.: 42 99999-0000."}
    lista = [c for c in contatos() if _norm(c["nome"]) != _norm(nome)]
    lista.append({"nome": nome, "numero": num})
    _gravar(lista)
    return {"ok": True, "mensagem": f"Contato {nome} salvo: {formatar(num)}."}


def remover_contato(nome: str) -> dict[str, Any]:
    lista = contatos()
    resto = [c for c in lista if _norm(c["nome"]) != _norm(nome)]
    if len(resto) == len(lista):
        return {"ok": False, "mensagem": f"Não encontrei o contato {nome}."}
    _gravar(resto)
    return {"ok": True, "mensagem": f"Contato {nome} removido."}


def achar(contato: str) -> list[dict[str, str]]:
    """Número direto, nome exato ou parte do nome (sem acento, sem maiúsculas)."""
    num = normalizar_numero(contato) if re.search(r"\d{8}", re.sub(r"\D", "", contato or "")) else None
    if num:
        return [{"nome": formatar(num), "numero": num}]
    alvo = re.sub(r"^(o |a |pro |pra |para (o |a )?)", "", _norm(contato)).strip()
    lista = contatos()
    exatos = [c for c in lista if _norm(c["nome"]) == alvo]
    if exatos:
        return exatos
    palavras = alvo.split()
    return [c for c in lista if palavras and all(p in _norm(c["nome"]).split() or p in _norm(c["nome"]) for p in palavras)]


def resolver(contato: str) -> tuple[dict[str, str] | None, str]:
    """Número direto ou contato salvo no JARVIS. Sem isso, o nome é procurado dentro do WhatsApp (Web)."""
    achados = achar(contato)
    if len(achados) == 1:
        return achados[0], ""
    if len(achados) > 1:
        nomes = ", ".join(c["nome"] for c in achados[:5])
        return None, f"Tenho mais de um contato parecido com {contato}: {nomes}. Qual deles?"
    return None, ""


def texto_confirmacao(args: dict[str, Any]) -> str:
    """Pré-visualização da ferramenta: termina com '?' (pede confirmação) ou explica por que não dá."""
    nome = " ".join(str(args.get("contato", "")).split())
    msg = " ".join(str(args.get("mensagem", "")).split())
    if not nome:
        return "Falta o destinatário: diga o nome do contato ou do grupo."
    if not msg:
        return "Falta o texto da mensagem: diga o que devo enviar."
    c, erro = resolver(nome)
    if erro:
        return erro
    if c is not None:
        return f"Enviar no WhatsApp para {c['nome']} ({formatar(c['numero'])}): “{msg[:300]}”?"
    if modo() == "app":
        return (f"Não tenho o número de {nome}, e pelo app do PC só consigo mandar para números. Salve com "
                f"\"salva o contato {nome} 42 99999-0000\" ou mude o WhatsApp para o modo Web em Integrações.")
    return f"Enviar no WhatsApp para “{nome}” (vou procurar no seu WhatsApp, contato ou grupo): “{msg[:300]}”?"


def escolher(nome: str, titulos: list[str]) -> tuple[str | None, list[str]]:
    """Qual conversa da busca do WhatsApp é a pedida. Devolve (escolhida, candidatas). Função pura, testável.

    Exato > começa com o nome > contém todas as palavras > parecido (ex.: "Marine" x "Marini"). Se houver mais
    de uma igualmente boa, não escolhe: o JARVIS pergunta qual (nunca arrisca mandar para a conversa errada).
    """
    import difflib

    alvo = _norm(nome)
    vistos: list[str] = []
    for t in titulos:
        if t and t not in vistos:
            vistos.append(t)
    limpos = {t: _norm(re.sub(r"[^\w\s]", " ", t)) for t in vistos}
    exatos = [t for t in vistos if limpos[t] == alvo]
    if len(exatos) == 1:
        return exatos[0], exatos
    if len(exatos) > 1:
        return None, exatos
    for filtro in (lambda t: limpos[t].startswith(alvo + " "),
                   lambda t: all(p in limpos[t].split() for p in alvo.split())):
        achados = [t for t in vistos if filtro(t)]
        if len(achados) == 1:
            return achados[0], achados
        if len(achados) > 1:
            return None, achados

    def nota(t: str) -> float:
        palavras = limpos[t].split() or [""]
        inteiro = difflib.SequenceMatcher(None, alvo, limpos[t]).ratio()
        parcial = max(difflib.SequenceMatcher(None, alvo, " ".join(palavras[i:i + len(alvo.split())])).ratio()
                      for i in range(len(palavras)))
        return max(inteiro, parcial * 0.95)          # parte de um nome vale um pouco menos que o nome inteiro

    notas = sorted(((nota(t), t) for t in vistos), reverse=True)
    bons = [(n, t) for n, t in notas if n >= 0.8]
    if len(bons) == 1 or (len(bons) > 1 and bons[0][0] - bons[1][0] >= 0.1):
        return bons[0][1], [t for _, t in bons]
    # A busca do WhatsApp também traz conversas onde o nome só aparece numa mensagem: essas não contam
    return None, [t for _, t in bons] or [t for n, t in notas[:5] if n >= 0.7]


def termos_alternativos(nome: str) -> list[str]:
    """Outras buscas quando o nome não aparece: o começo da palavra mais longa ("Rosana" acha "Rosane")."""
    palavras = sorted((p for p in _norm(re.sub(r"[^\w\s]", " ", nome)).split() if len(p) >= 5), key=len, reverse=True)
    return [palavras[0][:4]] if palavras else []


def _procurar(ponte: Any, nome: str) -> tuple[dict[str, Any], str | None, list[str]]:
    """Busca a conversa; se nada bater com o nome, tenta de novo com um pedaço dele (erro de grafia/voz)."""
    r = ponte.pedir({"type": "buscar", "nome": nome})
    if not r.get("ok"):
        return r, None, []
    escolhida, candidatas = escolher(nome, r.get("titulos") or [])
    if escolhida is None and not candidatas:
        for termo in termos_alternativos(nome):
            r2 = ponte.pedir({"type": "buscar", "nome": termo})
            if r2.get("ok"):
                escolhida, candidatas = escolher(nome, r2.get("titulos") or [])
                if escolhida or candidatas:
                    break
    return r, escolhida, candidatas


# --------------------------------------------------------------------------- envio
def modo() -> str:
    """auto = WhatsApp Web controlado pelo JARVIS (acha contatos e grupos pelo nome e confere o envio)."""
    return "app" if settings.whatsapp_modo == "app" else "web"


# --------------------------------------------------------------------------- ponte com a extensão do navegador
class _Ponte:
    """Conexão com a extensão (vive no loop do servidor). pedir() é chamado da thread da ferramenta."""

    def __init__(self) -> None:
        self.ws: Any = None
        self.loop: Any = None
        self.info: dict[str, Any] = {}
        self.pendentes: dict[str, Any] = {}

    def conectada(self) -> bool:
        return self.ws is not None

    def ligar(self, ws: Any, loop: Any) -> None:
        self.ws, self.loop = ws, loop

    def desligar(self, ws: Any) -> None:
        if self.ws is ws:
            self.ws, self.info = None, {}
            for f in self.pendentes.values():
                if not f.done():
                    f.set_result({"ok": False, "motivo": "desconectou"})
            self.pendentes.clear()

    def resultado(self, dados: dict[str, Any]) -> None:
        f = self.pendentes.pop(str(dados.get("id")), None)
        if f is not None and not f.done():
            f.set_result(dados)

    def pedir(self, msg: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        import asyncio
        import uuid
        if not self.conectada():
            return {"ok": False, "motivo": "sem_extensao"}
        ident = uuid.uuid4().hex[:10]

        async def _ida() -> dict[str, Any]:
            futuro = self.loop.create_future()
            self.pendentes[ident] = futuro
            await self.ws.send_json({**msg, "id": ident})
            return await asyncio.wait_for(futuro, timeout)

        try:
            return asyncio.run_coroutine_threadsafe(_ida(), self.loop).result(timeout + 5)
        except Exception as exc:
            self.pendentes.pop(ident, None)
            return {"ok": False, "motivo": "tempo", "erro": str(exc)}


PONTE = _Ponte()


class _PonteEmbutida:
    """O WhatsApp aberto dentro do JARVIS (painel APPS): o mesmo script da extensão, pelo protocolo do navegador."""

    def estado(self) -> str | None:
        """pronto | carregando | login | None (não está aberto)."""
        from .embutidos import EMBUTIDOS
        try:
            return EMBUTIDOS.whatsapp({"type": "estado"}, timeout=8).get("estado")
        except Exception:
            return None

    def conectada(self) -> bool:
        return self.estado() == "pronto"

    def pedir(self, msg: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
        from .embutidos import EMBUTIDOS
        r = EMBUTIDOS.whatsapp(msg, timeout)
        if r.get("motivo") in ("login", "carregando"):          # mensagem que fala do WhatsApp de dentro
            r["motivo"] = "embutido_" + r["motivo"]
        return r


EMBUTIDA = _PonteEmbutida()


def _ponte() -> Any:
    """Quem opera o WhatsApp: o de dentro do JARVIS (se estiver logado) ou a extensão no seu navegador.
    Logo depois do QR code o WhatsApp de dentro passa um tempo "carregando as conversas": espera um pouco por ele."""
    estado = EMBUTIDA.estado()
    fim = time.monotonic() + 20
    while estado == "carregando" and time.monotonic() < fim:
        time.sleep(1.5)
        estado = EMBUTIDA.estado()
    if estado == "pronto" or (estado and not PONTE.conectada()):
        return EMBUTIDA                                 # pronto, ou o único que existe (a resposta explica o que falta)
    return PONTE
EXTENSAO_ORIGEM = Path(__file__).parent / "extensao_whatsapp"
EXTENSAO_DESTINO = USER_DIR / "extensao-whatsapp"

MOTIVOS = {
    "login": "O WhatsApp Web do seu navegador está desconectado. Abra web.whatsapp.com e entre de novo.",
    "sem_script": "A aba do WhatsApp foi aberta antes da extensão: recarregue a aba (F5) e peça de novo.",
    "sem_busca": "Não achei a caixa de pesquisa do WhatsApp Web (o layout mudou?).",
    "sumiu_da_busca": "A conversa sumiu da pesquisa antes de eu abrir; tente de novo.",
    "sem_permissao": "Não consigo escrever nessa conversa (em grupos, às vezes só os admins podem).",
    "nao_digitou": "O WhatsApp não aceitou o texto que digitei; nada foi enviado.",
    "tempo": "O WhatsApp Web demorou para responder; tente de novo.",
    "desconectou": "A extensão desconectou no meio do envio; confira a conversa.",
    "embutido_login": "O WhatsApp aqui dentro do JARVIS não está conectado: abra o painel APPS e escaneie o QR code.",
    "embutido_carregando": "O WhatsApp aqui dentro do JARVIS ainda está carregando as conversas; peça de novo em um instante.",
}


def preparar_pasta_extensao() -> Path:
    """Mantém a extensão atualizada numa pasta fixa (o .exe se descompacta numa pasta temporária a cada vez,
    então o navegador precisa de um lugar que não muda). Chamado ao abrir o JARVIS."""
    import shutil
    EXTENSAO_DESTINO.mkdir(parents=True, exist_ok=True)
    for arq in EXTENSAO_ORIGEM.iterdir():
        if arq.is_file():
            shutil.copy2(arq, EXTENSAO_DESTINO / arq.name)
    icone = Path(__file__).parent / "web" / "icon.png"
    if icone.is_file() and not (EXTENSAO_DESTINO / "icon.png").is_file():
        shutil.copy2(icone, EXTENSAO_DESTINO / "icon.png")
    return EXTENSAO_DESTINO


def versao_extensao() -> str:
    try:
        return json.loads((EXTENSAO_ORIGEM / "manifest.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return ""


def instalar_extensao() -> dict[str, Any]:
    """Copia a extensão para uma pasta fixa (%APPDATA%\\JARVIS\\extensao-whatsapp) e abre a tela de extensões."""
    from .app import browsers
    preparar_pasta_extensao()
    navegadores = browsers()
    if navegadores:                                    # abre chrome://extensions no SEU perfil (sem --user-data-dir)
        exe = navegadores[0]
        pagina = "edge://extensions/" if "edge" in exe.lower() else "chrome://extensions/"
        subprocess.Popen([exe, pagina])
    if IS_WINDOWS:
        os.startfile(str(EXTENSAO_DESTINO))  # type: ignore[attr-defined]
    return {"ok": True, "pasta": str(EXTENSAO_DESTINO)}


def _abrir_whatsapp_no_navegador() -> bool:
    """Sem aba do WhatsApp aberta: abre no navegador padrão e espera a extensão dizer que carregou."""
    import webbrowser
    webbrowser.open("https://web.whatsapp.com/")
    fim = time.monotonic() + 40
    while time.monotonic() < fim:
        time.sleep(1.5)
        r = PONTE.pedir({"type": "estado"}, timeout=5)
        if r.get("ok") and r.get("estado") == "pronto":
            time.sleep(1.5)
            return True
        if r.get("estado") == "login":
            return False
    return False


def _enviar_extensao(nome: str, mensagem: str, ponte: Any = None) -> dict[str, Any]:
    PONTE = ponte or globals()["PONTE"]                 # noqa: N806 — mesma interface: extensão ou WhatsApp embutido
    r, escolhida, candidatas = _procurar(PONTE, nome)
    if r.get("motivo") == "sem_aba":
        if not _abrir_whatsapp_no_navegador():
            return {"ok": False, "mensagem": "Abri o WhatsApp Web no seu navegador, mas ele não carregou logado a tempo."}
        r, escolhida, candidatas = _procurar(PONTE, nome)
    if not r.get("ok"):
        return {"ok": False, "motivo": r.get("motivo"), "mensagem": MOTIVOS.get(r.get("motivo"), "O WhatsApp Web não respondeu.")}
    if escolhida is None:
        PONTE.pedir({"type": "limpar"}, timeout=8)
        if candidatas:
            return {"ok": False, "motivo": "ambiguo", "candidatas": candidatas[:5],
                    "mensagem": f"Achei mais de uma conversa parecida com {nome}: {', '.join(candidatas[:5])}. Qual delas?"}
        return {"ok": False, "motivo": "nao_achou", "mensagem": f"Não achei {nome} no seu WhatsApp (nem contato nem grupo). "
                "Se ela estiver salva com outro nome, me diga qual (ou crie um apelido)."}
    r = PONTE.pedir({"type": "enviar_para", "titulo": escolhida, "mensagem": mensagem}, timeout=60)
    if r.get("ok"):
        conferido = r.get("conferido", True)
        return {"ok": True, "conferido": conferido, "conversa": escolhida,
                "mensagem": f"Mensagem enviada para {escolhida}" + (" (conferida no seu WhatsApp Web)." if conferido else
                            ". A caixa de texto esvaziou, mas não consegui ler a conversa para conferir.")}
    if r.get("motivo") == "conversa_errada":
        return {"ok": False, "mensagem": f"Abri uma conversa, mas não era {escolhida}; não enviei nada."}
    if r.get("motivo") == "nao_confirmou":
        return {"ok": False, "conversa": escolhida, "mensagem": f"Escrevi para {escolhida}, mas não consegui confirmar o envio. "
                                                                "Confira a conversa."}
    return {"ok": False, "mensagem": MOTIVOS.get(r.get("motivo"), "O WhatsApp Web não respondeu.")}


# --------------------------------------------------------------------------- leitura (pela extensão)
SEM_EXTENSAO = ("Para ler as suas mensagens, abra o WhatsApp dentro do JARVIS (painel APPS, faça o login uma vez) "
                "ou instale a extensão do JARVIS no navegador em que o WhatsApp Web está aberto.")


def pendentes(limite: int = 10) -> dict[str, Any]:
    """Conversas com mensagens não lidas (nome, prévia da última, horário e quantas). Não abre nenhuma conversa,
    então nada é marcado como lido."""
    PONTE = _ponte()                                    # noqa: N806
    if not PONTE.conectada():
        return {"ok": False, "mensagem": SEM_EXTENSAO}
    r = PONTE.pedir({"type": "nao_lidas", "limite": max(1, min(int(limite or 10), 30))})
    if r.get("motivo") == "sem_aba" and _abrir_whatsapp_no_navegador():
        r = PONTE.pedir({"type": "nao_lidas", "limite": limite})
    if not r.get("ok"):
        return {"ok": False, "mensagem": MOTIVOS.get(r.get("motivo"), "O WhatsApp Web não respondeu.")}
    conversas = r.get("conversas") or []
    card = {"titulo": "WhatsApp — não lidas", "tipo": "lista", "fonte": "seu WhatsApp Web",
            "itens": [{"titulo": c["conversa"], "detalhe": c.get("previa", ""),
                       "valor": f"{c['nao_lidas']} · {c.get('quando', '')}".strip(" ·")} for c in conversas]}
    return {"ok": True, "conversas": conversas, "total": r.get("total", 0), "card": card if conversas else None}


def ler(contato: str, quantidade: int = 10) -> dict[str, Any]:
    """Últimas mensagens de uma conversa. Abrir a conversa faz o WhatsApp marcá-la como lida."""
    PONTE = _ponte()                                    # noqa: N806
    if not PONTE.conectada():
        return {"ok": False, "mensagem": SEM_EXTENSAO}
    from .avisos import nome_para
    nome = nome_para(" ".join((contato or "").split()))
    c, _ = resolver(nome)
    alvo = nome if c is None else c["nome"]
    r, escolhida, candidatas = _procurar(PONTE, alvo)
    if not r.get("ok"):
        return {"ok": False, "mensagem": MOTIVOS.get(r.get("motivo"), "O WhatsApp Web não respondeu.")}
    if escolhida is None:
        PONTE.pedir({"type": "limpar"}, timeout=8)
        if candidatas:
            return {"ok": False, "motivo": "ambiguo", "candidatas": candidatas[:5],
                    "mensagem": f"Achei mais de uma conversa parecida com {alvo}: {', '.join(candidatas[:5])}. Qual delas?"}
        return {"ok": False, "mensagem": f"Não achei {alvo} no seu WhatsApp (nem contato nem grupo)."}
    r = PONTE.pedir({"type": "ler_conversa", "titulo": escolhida, "n": max(1, min(int(quantidade or 10), 30))}, timeout=40)
    if not r.get("ok"):
        return {"ok": False, "mensagem": MOTIVOS.get(r.get("motivo"), "Não consegui abrir a conversa.")}
    msgs = r.get("mensagens") or []
    card = {"titulo": f"WhatsApp — {escolhida}", "tipo": "lista", "fonte": "seu WhatsApp Web",
            "itens": [{"titulo": ("Você" if m.get("minha") else m.get("autor") or escolhida), "detalhe": m.get("texto", ""),
                       "valor": (m.get("quando") or "").split(",")[0]} for m in msgs]}
    return {"ok": True, "conversa": escolhida, "mensagens": msgs, "card": card if msgs else None}


def enviar(contato: str, mensagem: str) -> dict[str, Any]:
    from .avisos import nome_para
    nome = nome_para(" ".join((contato or "").split()))           # apelido ("Bia") -> nome da conversa
    mensagem = (mensagem or "").strip()
    if not nome or not mensagem:
        return {"ok": False, "mensagem": "Preciso do contato (ou grupo) e do texto da mensagem."}
    c, erro = resolver(nome)
    if erro:
        return {"ok": False, "mensagem": erro}
    m = modo()
    log.info("WhatsApp (%s): enviando para %s", m, c["nome"] if c else nome)
    if m == "app":
        if c is None:
            return {"ok": False, "mensagem": f"Pelo app do PC só consigo mandar para números; não tenho o de {nome}."}
        r = _enviar_app(c["numero"], mensagem)
    elif (ponte := _ponte()).conectada():
        # Contato salvo no JARVIS: pesquisa pelo nome salvo (é como está no celular); número direto: pelos dígitos
        alvo = nome if c is None else (c["nome"] if not c["nome"].startswith("+") else c["numero"][-8:])
        r = _enviar_extensao(alvo, mensagem, ponte)
        if not r.get("ok") and r.get("motivo") == "nao_achou" and c is not None:
            r = _enviar_web(mensagem, numero=c["numero"])          # número fora da agenda: janela própria
    else:
        r = _enviar_web(mensagem, numero=c["numero"] if c else None, nome=None if c else nome)
    if r.get("motivo") == "ambiguo":
        r["texto"] = mensagem                          # para continuar o mesmo envio depois do "qual delas?"
    destino = r.get("conversa") or (c["nome"] if c else nome)
    if r.get("ok"):
        r["mensagem"] = r.get("mensagem") or f"Mensagem enviada para {destino}."
    return {**r, "contato": destino, "via": "app" if m == "app" else "WhatsApp Web"}


# -- app do PC (só para números; o app não deixa ler a tela) ---------------------
def _janela_whatsapp() -> int:
    import ctypes
    user32 = ctypes.windll.user32
    achadas: list[int] = []
    Enum = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if buf.value == "WhatsApp" and cls.value.startswith("WinUIDesktop"):
                achadas.append(hwnd)
        return True

    user32.EnumWindows(Enum(cb), 0)
    return achadas[0] if achadas else 0


def _enviar_app(numero: str, mensagem: str) -> dict[str, Any]:
    import ctypes

    from . import desktop
    user32 = ctypes.windll.user32
    os.startfile(f"whatsapp://send?phone={numero}&text={urllib.parse.quote(mensagem)}")  # type: ignore[attr-defined]
    fim = time.monotonic() + 20
    hwnd = 0
    while time.monotonic() < fim and not hwnd:
        time.sleep(0.5)
        hwnd = _janela_whatsapp()
    if not hwnd:
        return {"ok": False, "mensagem": "O WhatsApp do PC não abriu. Ele está instalado e conectado?"}
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    time.sleep(3.0)
    if user32.GetForegroundWindow() != hwnd:
        desktop._para_frente(hwnd)
        time.sleep(0.4)
    if user32.GetForegroundWindow() != hwnd:             # nunca aperta Enter em outra janela
        return {"ok": False, "mensagem": "Deixei a mensagem digitada no WhatsApp, mas não consegui trazê-lo para a "
                                         "frente para enviar. Aperte Enter lá."}
    user32.keybd_event(0x0D, 0, 0, 0)
    user32.keybd_event(0x0D, 0, 2, 0)
    return {"ok": True, "conferido": False,
            "mensagem": "Enviei pelo WhatsApp do PC (o app não me deixa conferir; dê uma olhada na conversa)."}


# -- WhatsApp Web controlado pelo CDP -------------------------------------------
PERFIL_WEB = USER_DIR / "whatsapp-web"
WEB_URL = os.environ.get("JARVIS_WHATSAPP_URL", "https://web.whatsapp.com")

JS_ESTADO = r"""
(() => {
  const txt = document.body ? document.body.innerText : "";
  if (document.querySelector('canvas[aria-label*="QR"], div[data-ref] canvas') ||
      /Escaneie o QR|Scan the QR|Use o WhatsApp no seu computador|Use WhatsApp on your computer|Conectar com n[uú]mero/i.test(txt))
    return {estado: "login"};
  if (/inv[aá]lido|invalid/i.test(txt) && /url/i.test(txt)) return {estado: "invalido"};
  const box = document.querySelector('#main footer [contenteditable="true"]') || document.querySelector('footer [contenteditable="true"]');
  const lista = document.querySelector('#pane-side, #side');
  return {estado: box ? "conversa" : lista ? "lista" : "carregando", texto: box ? box.innerText.trim() : ""};
})()
"""
# Caixa de pesquisa da lista de conversas (contenteditable nas versões atuais, <input> em algumas)
JS_BUSCA = r"""
(() => {
  const el = document.querySelector('#side [contenteditable="true"]') || document.querySelector('#side input[type="text"]') ||
             document.querySelector('[aria-label*="esquis"][contenteditable="true"], input[aria-label*="esquis"], [aria-label*="earch"][contenteditable="true"]');
  if (!el) return null;
  el.scrollIntoView({block: "center"});
  const r = el.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, tag: el.tagName};
})()
"""
# Resultados da busca: título de cada conversa e onde clicar
JS_RESULTADOS = r"""
(() => {
  const rows = [...document.querySelectorAll('#pane-side [role="listitem"], #pane-side [role="row"], #pane-side [role="gridcell"]')];
  const out = [];
  for (const row of rows) {
    const span = row.querySelector('span[title]');
    const titulo = span ? span.getAttribute('title') : (row.innerText || "").split("\n")[0];
    const r = row.getBoundingClientRect();
    if (titulo && r.height > 0 && r.top >= 0 && r.top < innerHeight) out.push({titulo, x: r.left + r.width / 2, y: r.top + r.height / 2});
  }
  return out;
})()
"""
JS_CABECALHO = r"""
(() => {
  const h = document.querySelector('#main header');
  if (!h) return "";
  const s = h.querySelector('span[title], span[dir="auto"]');
  return (s ? (s.getAttribute('title') || s.innerText) : h.innerText.split("\n")[0]).trim();
})()
"""
JS_COMPOSER = r"""
(() => {
  const el = document.querySelector('#main footer [contenteditable="true"]') || document.querySelector('footer [contenteditable="true"]');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {x: r.left + Math.min(40, r.width / 2), y: r.top + r.height / 2};
})()
"""
JS_CONFERIR = r"""
(() => {
  const box = document.querySelector('#main footer [contenteditable="true"]') || document.querySelector('footer [contenteditable="true"]');
  const saidas = [...document.querySelectorAll('#main div.message-out, #main [data-id^="true_"], div.message-out')];
  const ultima = saidas.length ? saidas[saidas.length - 1].innerText : "";
  return {vazia: !box || !box.innerText.trim(), ultima: ultima.slice(0, 400)};
})()
"""


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CDP:
    def __init__(self, ws_url: str):
        from websockets.sync.client import connect
        self.ws = connect(ws_url, max_size=None, open_timeout=10)
        self.n = 0

    def call(self, method: str, tempo: float = 15, **params: Any) -> dict[str, Any]:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=tempo))
            if msg.get("id") == self.n:
                if "error" in msg:                       # ex.: o contexto da página sumiu (recarregou)
                    return {"erro_cdp": msg["error"]}
                return msg.get("result", {})

    def eval(self, js: str) -> Any:
        r = self.call("Runtime.evaluate", expression=js, returnByValue=True)
        return (r.get("result") or {}).get("value")

    def clicar(self, x: float, y: float) -> None:
        for tipo in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=tipo, x=x, y=y, button="left", clickCount=1)

    def tecla(self, key: str, code: str, vk: int, texto: str = "", mods: int = 0) -> None:
        self.call("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, windowsVirtualKeyCode=vk,
                  text=texto, modifiers=mods)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=mods)

    def digitar(self, texto: str) -> None:
        self.call("Input.insertText", text=texto)        # acentos e emojis entram certinho

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


class _JanelaWeb:
    """Abre o WhatsApp Web (perfil próprio, fora da tela) com a porta de controle só durante o envio."""

    def __init__(self, url: str, timeout: float):
        self.url, self.fim, self.cdp, self.manter, self.porta = url, time.monotonic() + timeout, None, False, 0
        self.perfil = PERFIL_WEB

    def __enter__(self) -> "_JanelaWeb":
        """Chrome primeiro; se não houver, se a empresa bloquear a depuração ou se não conectar, tenta o Edge."""
        from . import desktop
        from .app import browsers, perfil
        candidatos = [b for b in browsers() if not desktop.depuracao_bloqueada(b)]
        if not candidatos:
            raise RuntimeError("Não encontrei o Chrome nem o Edge liberados para abrir o WhatsApp Web.")
        for i, exe in enumerate(candidatos):
            self.perfil = perfil(PERFIL_WEB, exe)
            ultimo = i == len(candidatos) - 1
            limite = self.fim if ultimo else min(self.fim, time.monotonic() + 20)
            if self._abrir(exe, limite):
                log.info("WhatsApp Web aberto no %s", Path(exe).stem)
                return self
            log.warning("WhatsApp Web não conectou no %s; tentando o próximo navegador", Path(exe).stem)
            desktop.close_browser_windows(self.perfil)
        raise TimeoutError("O WhatsApp Web não abriu a tempo.")

    def _abrir(self, exe: str, limite: float) -> bool:
        from . import desktop
        desktop.close_browser_windows(self.perfil)       # janela antiga (ex.: do QR) sem a porta de controle
        time.sleep(0.8)
        self.porta = _porta_livre()
        self.perfil.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([exe, f"--app={self.url}", f"--user-data-dir={self.perfil}", f"--remote-debugging-port={self.porta}",
                          "--no-first-run", "--no-default-browser-check", "--window-position=-32000,-32000",
                          "--window-size=1200,860", "--disable-background-timer-throttling",
                          "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows"])
        host = urllib.parse.urlparse(WEB_URL).netloc or WEB_URL
        while time.monotonic() < limite:
            time.sleep(0.5)
            try:
                alvos = httpx.get(f"http://127.0.0.1:{self.porta}/json", timeout=2).json()
            except (httpx.HTTPError, ValueError):
                continue
            pagina = next((a for a in alvos if a.get("type") == "page" and host in a.get("url", "")), None)
            if pagina:
                self.cdp = _CDP(pagina["webSocketDebuggerUrl"])
                return True
        return False

    def tempo(self) -> bool:
        return time.monotonic() < self.fim

    def mostrar(self) -> None:
        """Traz a janela para a tela (para escanear o QR ou conferir) e deixa aberta."""
        self.manter = True
        try:
            v = httpx.get(f"http://127.0.0.1:{self.porta}/json/version", timeout=2).json()
            b = _CDP(v["webSocketDebuggerUrl"])
            alvo = next(a for a in httpx.get(f"http://127.0.0.1:{self.porta}/json", timeout=2).json() if a.get("type") == "page")
            win = b.call("Browser.getWindowForTarget", targetId=alvo["id"])["windowId"]
            b.call("Browser.setWindowBounds", windowId=win, bounds={"left": 120, "top": 60, "width": 1200, "height": 860,
                                                                    "windowState": "normal"})
            b.close()
        except Exception as exc:
            log.warning("Não consegui mostrar a janela do WhatsApp Web: %s", exc)

    def __exit__(self, *exc: Any) -> None:
        from . import desktop
        if self.cdp is not None:
            self.cdp.close()
        if not self.manter:
            desktop.close_browser_windows(self.perfil)


def _esperar(j: _JanelaWeb, quer: tuple[str, ...]) -> dict[str, Any]:
    estado: dict[str, Any] = {}
    while j.tempo():
        estado = j.cdp.eval(JS_ESTADO) or {}
        if estado.get("estado") in ("login", "invalido") or estado.get("estado") in quer:
            return estado
        time.sleep(0.8)
    return {"estado": "tempo"}


def _conferir_envio(j: _JanelaWeb, mensagem: str) -> bool:
    inicio = " ".join(mensagem.split())[:15]
    for _ in range(24):
        time.sleep(0.5)
        c = j.cdp.eval(JS_CONFERIR) or {}
        if c.get("vazia") and inicio in " ".join(c.get("ultima", "").split()):
            time.sleep(2)                                # sai do "relógio" para "enviado"
            return True
    return False


LOGIN_MSG = ("O WhatsApp Web precisa ser conectado uma vez neste PC: escaneie o QR code na janela que abri "
             "(no celular: WhatsApp → Aparelhos conectados → Conectar aparelho) e peça de novo.")


def _enviar_web(mensagem: str, numero: str | None = None, nome: str | None = None, timeout: float = 90) -> dict[str, Any]:
    url = (f"{WEB_URL}/send?phone={numero}&text={urllib.parse.quote(mensagem)}" if numero else WEB_URL)
    try:
        with _JanelaWeb(url, timeout) as j:
            if numero:                                   # número: a conversa já abre com o texto digitado
                est = _esperar(j, ("conversa",))
                while est.get("estado") == "conversa" and not est.get("texto") and j.tempo():
                    time.sleep(0.8)
                    est = j.cdp.eval(JS_ESTADO) or {}
            else:                                        # nome: procura na lista de conversas, contatos e grupos
                est = _esperar(j, ("lista", "conversa"))
            if est.get("estado") == "login":
                j.mostrar()
                return {"ok": False, "mensagem": LOGIN_MSG}
            if est.get("estado") == "invalido":
                return {"ok": False, "mensagem": "O WhatsApp diz que esse número não tem conta no WhatsApp."}
            if est.get("estado") == "tempo":
                return {"ok": False, "mensagem": "O WhatsApp Web demorou demais para carregar; tente de novo."}
            conversa = None
            if nome:
                busca = j.cdp.eval(JS_BUSCA)
                if not busca:
                    j.mostrar()
                    return {"ok": False, "mensagem": "Não achei a caixa de pesquisa do WhatsApp Web (o layout mudou?)."}
                j.cdp.clicar(busca["x"], busca["y"])
                j.cdp.tecla("a", "KeyA", 65, mods=2)       # Ctrl+A e apaga o que houver
                j.cdp.tecla("Backspace", "Backspace", 8)
                j.cdp.digitar(nome)
                resultados: list[dict[str, Any]] = []
                for _ in range(8):                         # espera os resultados estabilizarem
                    time.sleep(0.7)
                    novos = j.cdp.eval(JS_RESULTADOS) or []
                    if novos and [r["titulo"] for r in novos] == [r["titulo"] for r in resultados]:
                        break
                    resultados = novos
                escolhida, candidatas = escolher(nome, [r["titulo"] for r in resultados])
                if escolhida is None:
                    if candidatas:
                        return {"ok": False, "mensagem": f"Achei mais de uma conversa parecida com {nome}: "
                                + ", ".join(candidatas[:5]) + ". Diga o nome exato."}
                    return {"ok": False, "mensagem": f"Não achei {nome} no seu WhatsApp (nem contato nem grupo)."}
                alvo = next(r for r in resultados if r["titulo"] == escolhida)
                j.cdp.clicar(alvo["x"], alvo["y"])
                cabecalho = ""
                for _ in range(12):                        # confere que abriu a conversa certa
                    time.sleep(0.5)
                    cabecalho = j.cdp.eval(JS_CABECALHO) or ""
                    if _norm(cabecalho) == _norm(escolhida):
                        break
                if _norm(cabecalho) != _norm(escolhida):
                    return {"ok": False, "mensagem": f"Abri uma conversa, mas não era {escolhida}; não enviei nada."}
                conversa = escolhida
                comp = j.cdp.eval(JS_COMPOSER)
                if not comp:
                    return {"ok": False, "mensagem": f"Não consigo escrever em {escolhida} (você é admin do grupo?)."}
                j.cdp.clicar(comp["x"], comp["y"])
                j.cdp.digitar(mensagem)
                time.sleep(0.4)
            j.cdp.tecla("Enter", "Enter", 13, texto="\r")
            if _conferir_envio(j, mensagem):
                return {"ok": True, "conferido": True, "conversa": conversa,
                        "mensagem": f"Mensagem enviada{' para ' + conversa if conversa else ''} pelo WhatsApp Web (conferida)."}
            j.mostrar()
            return {"ok": False, "conversa": conversa, "mensagem": "Escrevi a mensagem, mas não consegui confirmar o envio. "
                                                                   "Deixei a janela do WhatsApp Web aberta para você conferir."}
    except (RuntimeError, TimeoutError) as exc:
        return {"ok": False, "mensagem": str(exc)}
    except Exception as exc:
        log.exception("Falha no WhatsApp Web")
        return {"ok": False, "mensagem": f"Falha ao controlar o WhatsApp Web ({exc.__class__.__name__})."}


def contato(acao: str, nome: str = "", numero: str = "") -> dict[str, Any]:
    """Ferramenta: salvar, remover ou listar contatos do WhatsApp."""
    if acao == "salvar":
        return salvar_contato(nome, numero)
    if acao == "remover":
        return remover_contato(nome)
    lista = contatos()
    return {"ok": True, "total": len(lista), "contatos": [{"nome": c["nome"], "numero": formatar(c["numero"])} for c in lista]}
