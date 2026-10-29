"""Falas espontâneas do JARVIS: a saudação ao iniciar e as boas-vindas ao desbloquear a tela.

Ao bloquear (Win+L) guardamos as massivas ativas; ao desbloquear comparamos com as atuais e o JARVIS
conta o que mudou. As frases são montadas aqui (sem chamar a IA), então saem na hora e funcionam
mesmo no modo offline; elas entram no histórico para a conversa continuar dali ("quero os detalhes").
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from . import massivas
from .config import settings
from .logger import log

MIN_BLOQUEIO = 30          # segundos: bloqueios rápidos (sem sair do PC) não geram boas-vindas
estado: dict[str, Any] = {"saudou_inicio": False, "antes": None}


def _saudacao() -> str:
    h = datetime.now().hour
    return "Bom dia" if 5 <= h < 12 else "Boa tarde" if 12 <= h < 18 else "Boa noite"


def _quem() -> str:
    return settings.nome or settings.tratamento


def inicio() -> str:
    """Primeira fala desde que o JARVIS abriu (ex.: ligou o PC). Recarregar a página dá uma fala curta."""
    if estado["saudou_inicio"]:
        return f"{_saudacao()}, {settings.tratamento}. Todos os sistemas estão online e operacionais."
    estado["saudou_inicio"] = True
    return (f"{_saudacao()}, {_quem()}. Todos os sistemas online. Quer que eu fale as principais notícias do dia, "
            "traga as atualizações do trabalho ou coloque uma música?")


def ao_bloquear() -> None:
    estado["antes"] = massivas.snapshot()


def ao_desbloquear() -> str:
    antes, estado["antes"] = estado["antes"], None
    depois = massivas.snapshot()
    texto = f"Bem-vindo de volta, {_quem()}."
    mudou = massivas.mudancas(antes, depois)
    if mudou:
        texto += " " + mudou
        aviso = massivas.aviso_sincronizacao()
        if aviso:
            texto += " Mas atenção: o MHnet Notifier não está sincronizando, então pode haver mudanças que não vi."
        elif not mudou.startswith("Nenhuma"):
            texto += " Quer os detalhes?"
    return texto


def vigiar_bloqueio(anunciar: Callable[[str, str], None]) -> bool:
    """Liga o aviso de bloqueio/desbloqueio. anunciar(texto, contexto) fala na interface."""
    from .desktop import LockWatcher

    def desbloqueou(duracao: float) -> None:
        if duracao < MIN_BLOQUEIO:
            estado["antes"] = None
            return
        anunciar(ao_desbloquear(), f"O usuário desbloqueou o PC depois de {int(duracao // 60)} min fora.")

    ok = LockWatcher(ao_bloquear, desbloqueou).start()
    log.info("Boas-vindas ao desbloquear a tela: %s", "ativas" if ok else "indisponíveis neste sistema")
    return ok
