"""Sessão de conversa: histórico, cérebro atual, confirmações e fallback offline."""
from __future__ import annotations

import re
from typing import Any, Callable

from .brain import LABELS, Brain, BrainError, create_brain
from .brain.local import LocalBrain, normalize
from .config import settings
from .logger import log
from .tools import ToolExecutor

_YES = re.compile(r"^(sim|confirmo|confirmar|confirma|pode|pode sim|autorizo|positivo|afirmativo|claro)\b")
# "mostra a letra", "letra dessa música", "fecha a letra" (fora: letra de câmbio, letra maiúscula...)
_LETRA = re.compile(r"\bletra\b")
_NAO_LETRA = re.compile(r"\bletra (de cambio|maiuscula|minuscula|da lei|de credito)\b")
_FECHA_LETRA = re.compile(r"\b(fech|escond|ocult|tir|remov|some)\w*")
# "acorda", "acorde", "desperta", "aparece", "mostra a interface", "vem pra frente"
_ACORDA = re.compile(r"^(?:(?:ei |e ai |oi )?jarvis\W*)?(?:acord\w*|despert\w*|aparec\w*|apareca|vem (?:pra|para) (?:frente|tela)|"
                     r"(?:mostr|abr|traz|tra)\w* (?:a |sua )?(?:interface|janela|tela)(?: (?:pra|para) (?:frente|mim))?)"
                     r"(?: (?:jarvis|ai|agora|por favor|pfv))*[ .!?]*$")
# WhatsApp: "manda uma mensagem pra Maria dizendo que chego às 8" (uma etapa) ou
# "manda uma mensagem pro grupo da família" -> "o que devo dizer?" -> "que chego às 8" (duas etapas)
_WA_PALAVRA = re.compile(r"\b(mensagem|msg|zap|whats\w*|recado)\b")
_WA_INICIO = (r"^(?:(?:ei |oi )?jarvis\W*)?(?:mand|envi|escrev)\w*\s+(?:uma |um )?(?:mensagem |msg |zap |recado )?"
              r"(?:no whats\w* |pelo whats\w* |no zap )?(?:pro |pra |para (?:o |a )?|ao |a )")
_WA_COM_TEXTO = re.compile(_WA_INICIO + r"(.+?)\s*(?:,|:|\bdizendo\b|\bfalando\b|\bescrito\b|\bque\b)\s*(?:que )?(.+?)[.!]*$")
_WA_SO_CONTATO = re.compile(_WA_INICIO + r"(.+?)[.!?]*$")
_WA_FIM_CONTATO = re.compile(r"\s+(?:no|pelo|via) (?:whats\w*|zap)$", re.I)
_WA_GRUPO = re.compile(r"^(?:o |a )?grupo\s+(?:d[aeo]s?\s+)?", re.I)


def _limpa_contato(nome: str) -> str:
    """'grupo da Família no WhatsApp.' -> 'Família' (o nome que se pesquisa no WhatsApp)."""
    nome = nome.strip(" ,.!?")
    nome = _WA_FIM_CONTATO.sub("", nome).strip(" ,.!?")
    return _WA_GRUPO.sub("", nome).strip() or nome
_WA_PREFIXO_TEXTO = re.compile(r"^(?:(?:diz|fala|manda dizer|escreve|avisa)\w*\s+(?:que\s+)?|que\s+)")
# "manda o status report no grupo Supervisão", "envia o report de hoje", "posta o relatório no grupo X"
_REPORT_WA = re.compile(r"^(?:(?:ei |oi )?jarvis\W*)?(?:mand|envi|post|compartilh|dispar|joga)\w*\s+(?:o |a )?"
                        r"(?:status ?report|report|relatorio)(?: de hoje| do dia)?"
                        r"(?:\s+(?:no|pro|pra|para o|para|ao|na)\s+(?:grupo\s+(?:d[aeo]s?\s+)?)?(.+?))?"
                        r"(?:\s+(?:no|pelo) (?:whats\w*|zap))?[.!?]*$")
# "tenho mensagens?", "quais minhas mensagens pendentes", "quem me mandou mensagem no whatsapp"
_WA_PENDENTES = re.compile(r"\b(mensage\w*|msgs?|conversas?|zap)\b.*\b(pendente\w*|nao lida\w*|novas?|nao respondid\w*)\b|"
                           r"\b(tenho|tem|chegou|chegaram)\b.*\b(mensage\w*|msgs?|zap)\b|\bquem (me )?mandou (mensage\w*|msg|zap)")
# "o que a Marini mandou", "lê as mensagens do grupo X", "o que falaram no grupo da família"
_WA_LER = re.compile(r"^(?:(?:ei |oi )?jarvis\W*)?(?:"
                     r"(?:le|leia|ler|mostra|mostre|abre|abra)\w*\s+(?:as |a )?(?:(?:ultimas|novas)\s+)?(?:mensage\w*|msgs?|conversa)\s+(?:d[aeo]s?|com)\s+(?:grupo\s+(?:d[aeo]s?\s+)?)?(?P<a>.+?)"
                     r"|o que (?:o |a )?(?P<b>.+?) (?:mandou|disse|falou|escreveu|respondeu)"
                     r"|o que (?:falaram|mandaram|disseram) no (?:grupo\s+(?:d[aeo]s?\s+)?)?(?P<c>.+?))"
                     r"(?:\s+(?:no|pelo) (?:whats\w*|zap))?[.!?]*$")
_CANCELA = re.compile(r"^(?:nao|cancela\w*|deixa\w*|esquece|nada|aborta)\b")
_NO = re.compile(r"^(nao|cancela|cancelar|negativo|deixa|esquece|aborta)\b")


def failure_reason(exc: Exception) -> str:
    """Traduz o erro técnico do provedor para algo que dá para falar em voz alta."""
    text = str(exc).lower()
    if "não conectada" in text or "não instalada" in text:
        return "conta não conectada; use ⚙ → Contas → Conectar"
    if "limite de uso do seu plano" in text:
        return "limite de uso do seu plano atingido"
    if "429" in text or "quota" in text:
        return "limite de uso do plano gratuito atingido"
    if "503" in text or "high demand" in text or "overloaded" in text or "529" in text:
        return "servidores sobrecarregados"
    if "401" in text or "403" in text or "api key" in text:
        return "chave de API recusada"
    if "conexão" in text or "connect" in text or "timeout" in text or "demorou" in text:
        return "sem conexão ou resposta lenta"
    return "erro inesperado do provedor"


class Assistant:
    def __init__(self, emit: Callable[[dict[str, Any]], None] | None = None, brain: str | None = None):
        self.emit = emit or (lambda _e: None)
        self.executor = ToolExecutor(self.emit)
        self.history: list[dict[str, str]] = []
        self.local = LocalBrain()
        self.brain_name = ""
        self.brain: Brain = self.local
        self.set_brain(brain or settings.default_brain())
        self._wa_contato: tuple[str, float] | None = None      # "o que devo dizer para X?" aguardando o texto
        self._wa_escolha: tuple[list[str], str, float] | None = None   # "qual delas?" (candidatas, mensagem)
        self._report_sem_grupo: float = 0.0                    # "para qual grupo?" aguardando o nome
        self._wa_leitura: tuple[list[str], float] | None = None  # "qual delas?" ao ler uma conversa

    def set_brain(self, name: str) -> str:
        try:
            self.brain = self.local if name == "local" else create_brain(name)
            self.brain_name = name
        except BrainError as exc:
            log.warning("Não foi possível usar o cérebro %s: %s", name, exc)
            self.brain, self.brain_name = self.local, "local"
        return self.brain_name

    @property
    def brain_label(self) -> str:
        return LABELS.get(self.brain_name, self.brain_name)

    def reset(self) -> None:
        self.history.clear()

    def _remember(self, role: str, text: str) -> None:
        self.history.append({"role": role, "text": text})
        if len(self.history) > 60:
            self.history = self.history[-40:]

    def _whatsapp_pedido(self, text: str) -> str | None:
        """Pedido de WhatsApp vai direto para a ferramenta (a IA às vezes dizia que tinha pedido confirmação sem
        ter chamado nada). Devolve None quando o texto não é sobre WhatsApp."""
        import time
        n = normalize(text).strip()
        span = lambda m, g: text.strip()[m.start(g):m.end(g)].strip()  # noqa: E731
        if self._wa_leitura and time.monotonic() - self._wa_leitura[1] < 180 and len(n) < 60:
            from .whatsapp import escolher
            escolhida, _ = escolher(re.sub(r"^(?:(?:o |a )?nome (?:exato )?(?:e|eh)\s+|(?:e|eh|a |o )\s*)", "", n).strip(" .!"),
                                    self._wa_leitura[0])
            self._wa_leitura = None
            if escolhida:
                return self._whatsapp_ler(text, escolhida)
        if _WA_PENDENTES.search(n) and not _WA_COM_TEXTO.match(n):
            return self._whatsapp_ler(text, None)
        ml = _WA_LER.match(n)
        if ml:
            g = next(k for k in ("a", "b", "c") if ml.group(k))
            return self._whatsapp_ler(text, _limpa_contato(text.strip()[ml.start(g):ml.end(g)]))
        m = _REPORT_WA.match(n)
        if m or (self._report_sem_grupo and time.monotonic() - self._report_sem_grupo < 180 and len(n) < 60):
            grupo = span(m, 1) if m and m.group(1) else ("" if m else _WA_GRUPO.sub("", text.strip()).strip(" .!"))
            if not m and _CANCELA.match(n):
                self._report_sem_grupo = 0.0
                return self._responder(text, "Tudo bem, não vou mandar o report.")
            return self._report_whatsapp(text, _WA_FIM_CONTATO.sub("", grupo).strip(" .!"))
        if self._wa_escolha and time.monotonic() - self._wa_escolha[2] < 180:
            from .whatsapp import escolher
            candidatas, mensagem, _ = self._wa_escolha
            if _CANCELA.match(n):
                self._wa_escolha = None
                return self._responder(text, "Tudo bem, não vou enviar nada.")
            nome = re.sub(r"^(?:(?:o |a )?nome (?:exato )?(?:e|eh)\s+|(?:e|eh|pra|para|pro)\s+(?:o |a )?)", "", n)
            nome = re.sub(r"\s+(?:so isso|somente|apenas)$", "", nome).strip(" .!")
            escolhida, _ = escolher(nome, candidatas)
            if escolhida:
                self._wa_escolha = None
                return self._whatsapp_enviar(text, escolhida, mensagem)
        if self._wa_contato and time.monotonic() - self._wa_contato[1] < 180 and not _WA_SO_CONTATO.match(n):
            contato, self._wa_contato = self._wa_contato[0], None
            if _CANCELA.match(n):
                return self._responder(text, "Tudo bem, não vou enviar nada.")
            m = _WA_PREFIXO_TEXTO.match(n)
            return self._whatsapp_enviar(text, contato, text.strip()[m.end():] if m else text.strip())
        if not _WA_PALAVRA.search(n):
            return None
        m = _WA_COM_TEXTO.match(n)
        if m:
            return self._whatsapp_enviar(text, _limpa_contato(span(m, 1)), span(m, 2))
        m = _WA_SO_CONTATO.match(n)
        if m:
            contato = _limpa_contato(span(m, 1))
            self._wa_contato = (contato, time.monotonic())
            return self._responder(text, f"O que devo dizer para {contato}?")
        return None

    def _whatsapp_ler(self, text: str, contato: str | None) -> str:
        """Lê pelo WhatsApp Web (extensão) e responde com um resumo curto; a lista completa vai num card."""
        self._remember("user", text)
        if contato is None:
            r = self.executor.run("whatsapp_nao_lidas", {"limite": 10})
            if not r.get("ok"):
                reply = r.get("mensagem", "Não consegui ler o WhatsApp.")
            elif not r["conversas"]:
                reply = f"Nenhuma mensagem não lida no WhatsApp, {settings.tratamento}."
            else:
                cs = r["conversas"]
                partes = [f"{c['conversa']} ({c['nao_lidas']}): {c['previa'][:80]}" if c.get("previa") else
                          f"{c['conversa']} ({c['nao_lidas']})" for c in cs[:4]]
                mais = f" e mais {len(cs) - 4} conversa(s)" if len(cs) > 4 else ""
                reply = (f"Você tem {r['total']} mensagem(ns) não lida(s) em {len(cs)} conversa(s). "
                         + "; ".join(partes) + mais + ". Quer que eu leia alguma?")
        else:
            r = self.executor.run("whatsapp_ler_conversa", {"contato": contato, "quantidade": 10})
            if not r.get("ok"):
                reply = r.get("mensagem", "Não consegui ler essa conversa.")
                if r.get("motivo") == "ambiguo":
                    import time
                    self._wa_leitura = (r["candidatas"], time.monotonic())
            elif not r["mensagens"]:
                reply = f"Abri a conversa com {r['conversa']}, mas não encontrei mensagens de texto recentes."
            else:
                ultimas = [m for m in r["mensagens"] if not m.get("minha")][-3:] or r["mensagens"][-3:]
                falas = [f"{'você' if m.get('minha') else (m.get('autor') or r['conversa'])}: {m['texto'][:140]}" for m in ultimas]
                reply = f"Últimas de {r['conversa']} — " + " | ".join(falas) + ". A conversa completa está na tela."
        self._remember("assistant", reply)
        return reply

    def _report_whatsapp(self, text: str, grupo: str) -> str:
        import time
        self._remember("user", text)
        r = self.executor.run("enviar_status_report", {"grupo": grupo} if grupo else {})
        self._report_sem_grupo = 0.0
        if r.get("status") == "aguardando_confirmacao":
            reply = f"{r['pergunta']} Diga sim para enviar ou não para cancelar."
        else:
            reply = r.get("mensagem") or "Não consegui preparar o envio do report."
            if "Para qual grupo" in reply:
                self._report_sem_grupo = time.monotonic()
        self._remember("assistant", reply)
        return reply

    def oferecer_report(self) -> str:
        """O StatusRobot acabou de gerar o relatório: o JARVIS oferece mandar no grupo (pede o "sim")."""
        import time
        r = self.executor.run("enviar_status_report", {})
        if r.get("status") == "aguardando_confirmacao":
            return f"O status report ficou pronto. {r['pergunta']} Diga sim para enviar."
        self._report_sem_grupo = time.monotonic()
        return "O status report ficou pronto. Em qual grupo do WhatsApp devo mandar?"

    def _whatsapp_enviar(self, text: str, contato: str, mensagem: str) -> str:
        self._remember("user", text)
        r = self.executor.run("whatsapp_enviar", {"contato": contato, "mensagem": mensagem})
        if r.get("status") == "aguardando_confirmacao":
            reply = f"{r['pergunta']} Diga sim para enviar ou não para cancelar."
        else:
            reply = r.get("mensagem") or "Não consegui preparar a mensagem."
        self._remember("assistant", reply)
        return reply

    def _responder(self, text: str, reply: str) -> str:
        self._remember("user", text)
        self._remember("assistant", reply)
        return reply

    def _acordar(self, text: str) -> str:
        """Traz a interface para a frente na hora (sem esperar a IA)."""
        self._remember("user", text)
        r = self.executor.run("mostrar_interface", {})
        reply = (f"Às suas ordens, {settings.tratamento}." if r.get("ok") else r.get("mensagem", "Não consegui."))
        self._remember("assistant", reply)
        return reply

    def _letra(self, text: str) -> str:
        """Abre/fecha o quadro de letra direto (a letra vai só para a tela, nunca para a voz)."""
        fechar = bool(_FECHA_LETRA.search(normalize(text)))
        self._remember("user", text)
        r = self.executor.run("mostrar_letra", {"acao": "fechar" if fechar else "mostrar"})
        if fechar:
            reply = f"Quadro de letra fechado, {settings.tratamento}."
        elif r.get("ok"):
            extra = " Esta não tem sincronia, então mostro a letra inteira." if r.get("tipo") == "sem sincronia" else ""
            reply = f"A letra de {r['musica']} está na tela, acompanhando a música, {settings.tratamento}.{extra}"
        else:
            reply = r.get("mensagem") or "Não consegui abrir a letra."
        self._remember("assistant", reply)
        return reply

    def announce(self, text: str, contexto: str) -> None:
        """Fala espontânea do JARVIS (saudação, boas-vindas): entra no histórico para a conversa seguir dali."""
        self._remember("user", f"[sistema] {contexto}")
        self._remember("assistant", text)

    def resolve(self, action_id: str | None, approve: bool) -> str:
        """Resolve uma ação pendente (pelo id, ou a mais recente)."""
        r = self.executor.resolve(action_id, approve) if action_id else self.executor.resolve_latest(approve)
        if r is None:
            return "Não há nenhuma ação aguardando confirmação."
        if r.get("motivo") == "ambiguo" and r.get("candidatas"):      # "qual delas?" fica esperando a resposta
            import time
            self._wa_escolha = (r["candidatas"], r.get("texto", ""), time.monotonic())
        self.emit({"type": "confirm_resolved", "id": action_id})
        msg = r.get("mensagem") or ("Feito." if r.get("ok") else "Não foi possível concluir.")
        # Registra no histórico para o cérebro saber o desfecho
        self._remember("user", f"[sistema] O usuário {'aprovou' if approve else 'recusou'} a ação {r.get('tool')}.")
        self._remember("assistant", msg)
        return msg

    def chat(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        # Confirmação por voz/texto ("sim", "confirmar", "não") quando há ação pendente
        if self.executor.pending:
            n = normalize(text).strip(" .!")
            n = re.sub(r"^jarvis[, ]*", "", n)
            if _YES.match(n):
                return self.resolve(None, True)
            if _NO.match(n):
                return self.resolve(None, False)

        wa = self._whatsapp_pedido(text)
        if wa:
            return wa
        if _ACORDA.match(normalize(text).strip()):
            return self._acordar(text)
        if _LETRA.search(normalize(text)) and not _NAO_LETRA.search(normalize(text)):
            return self._letra(text)

        self._remember("user", text)
        try:
            reply = self.brain.respond(self.history, self.executor)
        except Exception as exc:  # rede caiu, chave inválida, cota... cai para o modo offline
            log.warning("Cérebro %s falhou: %s", self.brain_name, exc)
            motivo = failure_reason(exc)
            self.emit({"type": "warning", "mensagem": f"{self.brain_label} indisponível: {motivo}."})
            reply = self.local.respond(self.history, self.executor)
            if not self.local.understood:
                reply = (f"Perdão, {settings.tratamento}. Meu cérebro {self.brain_label} está indisponível agora "
                         f"({motivo}). Tente de novo em alguns segundos; comandos diretos, como 'status do sistema', "
                         "continuam funcionando.")
        reply = reply or f"Certamente, {settings.tratamento}."
        self._remember("assistant", reply)
        return reply
