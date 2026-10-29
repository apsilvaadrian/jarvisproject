"""Cérebro offline: reconhece comandos em português por expressões regulares.

Não precisa de internet nem de chave de API. Serve como modo de demonstração e
como reserva quando o provedor de IA estiver indisponível.
"""
from __future__ import annotations

import random
import re
import unicodedata
from datetime import datetime
from typing import Any, Callable

from .. import system_monitor as sm
from ..config import settings
from ..tools import ToolExecutor
from .base import Brain


def normalize(text: str) -> str:
    """Minúsculas e sem acentos, preservando o tamanho (para recortar o texto original)."""
    return "".join(unicodedata.normalize("NFKD", c)[0] for c in text.lower())


def _clean(text: str) -> str:
    text = text.strip().strip(".!?,; ")
    return re.sub(r"^(ok |ei |hey )?jarvis[,!.]?\s*", "", text, flags=re.I)


Handler = Callable[[re.Match, str, ToolExecutor], str]


class LocalBrain(Brain):
    name = "local"

    @property
    def t(self) -> str:
        """Como chamar o usuário: na maioria das vezes "senhor", às vezes pelo nome."""
        if settings.nome and random.random() < 0.3:
            return settings.nome
        return settings.tratamento

    def __init__(self) -> None:
        self.understood = True
        self.rules: list[tuple[re.Pattern, Handler]] = [
            (r"^(oi|ola|bom dia|boa tarde|boa noite|e ai|fala)\b", self._greet),
            (r"\b(obrigad|valeu)", lambda m, o, e: random.choice([f"Sempre às ordens, {self.t}.", f"Disponha, {self.t}."])),
            (r"(quem e voce|seu nome|o que voce (faz|pode))", self._about),
            (r"(que horas|hora atual|horas sao)", lambda m, o, e: f"São {datetime.now():%H:%M}, {self.t}."),
            (r"(que dia|data de hoje|qual a data)", lambda m, o, e: f"Hoje é {datetime.now():%d/%m/%Y}, {self.t}."),
            (r"^(?:\w+ )?(?:mand|envi|escrev)\w*\s+(?:uma |um )?(?:mensagem|msg|zap|whats\w*)\s+(?:no whats\w* |pelo whats\w* )?"
             r"(?:pro |pra |para (?:o |a )?|ao |a )(.+?)\s*(?:,|:|\bdizendo\b|\bfalando\b|\bque\b|\bcom\b)\s*(?:que )?(.+)$",
             self._whatsapp),
            (r"^(?:\w+ )?(?:salv|adicion|cadastr|guard)\w*\s+(?:o |a )?(?:contato|numero)\s+(?:d[aeo] )?(.+?)\s*"
             r"(?:,|:|com o numero|numero|com)?\s*(\+?\(?\d[\d ()-]{7,})$", self._whatsapp_salvar),
            # Cards da interface (antes de "toca/coloca" e "fecha/encerra", que usam os mesmos verbos)
            (r"\b(restaur|reset)\w* (o |os )?(layout|cards|paineis)", lambda m, o, e: self._say(e.run("controlar_cards", {"acao": "restaurar"}))),
            (r"^(?:\w+ )?(?:desafix|desfix|desprend|solt)\w*\s+(?:o |a |os )?(?:card |painel )?(?:d[aeo]s? )?(.+)$", self._cards("desafixar")),
            (r"^(?:\w+ )?(?:fix|prend|trav)\w*\s+(?:o |a |os )?(?:card |painel )?(?:d[aeo]s? )?(.+)$", self._cards("fixar")),
            (r"^(?:\w+ )?(?:ocult|escond|some com|esconda)\w*\s+(?:o |a |os )?(?:card |painel )?(?:d[aeo]s? )?(.+)$", self._cards("ocultar")),
            (r"^(?:\w+ )?(?:fech|tir|remov)\w*\s+(?:o |a )?(?:card|painel)\s+(?:d[aeo]s? )?(.+)$", self._cards("ocultar")),
            (r"^(?:\w+ )?(?:mostr|exib|reabr|volt)\w*\s+(?:o |a )?(?:card|painel)\s+(?:d[aeo]s? )?(.+)$", self._cards("mostrar")),
            (r"^(?:\w+ )?(?:mov|mud|pass|coloc|bot|lev|joga)\w*\s+(?:o |a |os )?(?:card |painel )?(?:d[aeo]s? )?(.+?)\s+"
             r"(?:para|pra|pro|no|na)\s+(?:a |o )?((?:coluna|lado|canto|extrema|ultima|primeira|segunda|terceira)?\s*(?:da |de )?"
             r"(?:esquerd[ao]|direit[ao]|extra|ultima|primeira|segunda|terceira)\w*)(?:.*?\b(topo|cima|inicio|fim|final|baixo))?",
             self._cards("mover")),
            (r"\bfech\w* (a |o quadro d[ae] )?letra", lambda m, o, e: self._say(e.run("mostrar_letra", {"acao": "fechar"}))),
            (r"\bletra\b", lambda m, o, e: self._say(e.run("mostrar_letra", {}))),
            (r"(o que (esta|ta) tocando|\bque musica)", self._now_playing),
            (r"\b(pausa|pause|pausar)\b", self._spotify("pausar")),
            (r"\b(proxima|pula|pular|avanca)\b", self._spotify("proxima")),
            (r"\b(musica anterior|volta a musica|anterior)\b", self._spotify("anterior")),
            (r"\b(continua|continuar|despausa|retoma)\b", self._spotify("continuar")),
            (r"volume (?:do spotify )?(?:em |para |a |no )?(\d{1,3})", self._volume),
            (r"(aumenta|sobe)\w* o volume", self._media("volume_mais")),
            (r"(abaixa|diminui|baixa)\w* o volume", self._media("volume_menos")),
            (r"\b(mudo|silencia)", self._media("mudo")),
            (r"^(?:(?:quero|toque|toca|tocar|coloque|coloca|bota|ponha)\s+)?(?:a |uma |alguma )?musica(?: ai| por favor)?$",
             self._any_music),
            (r"^(?:toque|toca|tocar|coloque|coloca|bota|ponha|ponha pra tocar)\s+(?:a |o |uma? )?(?:musica |playlist |artista )?(.+?)(?: no spotify)?$", self._play),
            (r"\bcancel\w* (o )?(desligamento|reinicio)", self._tool("cancelar_desligamento")),
            (r"\b(deslig|reinici)\w*(?: o)?(?: pc| computador| sistema)?(?: em (\d+) minutos?)?", self._power),
            (r"\bbloque\w* (a )?tela", self._tool("bloquear_tela")),
            (r"^(?:encerr|fech|mat|finaliz)\w*\s+(?:o |a )?(?:processo |programa |app )?(\S+)$", self._kill),
            # "abre o app de massivas" abre o MHnet Notifier (sem isto cairia na consulta de massivas)
            (r"^(?:abr|inici|execut|lanc)\w*\s+(?:o |a )?((?:app |aplicativo |programa )?(?:d[aeo]s? )?"
             r"(?:massivas?|mhnet(?: notifier)?|notifier))$", self._open),
            (r"(status ?robot|status report|\breport\b)", self._robot),
            (r"\b(status|diagnostico|como (esta|ta) o (pc|sistema|computador))", self._status),
            (r"\bmassiv", self._massivas),
            (r"\bnoticia", self._news),
            (r"(atualizac\w* (do|de|sobre o) trabalho|\btrabalho\b|^(as )?atualizac\w*$)", self._work),
            (r"(teste de velocidade|velocidade da (minha )?(internet|conexao)|testa\w* a (internet|conexao))", self._speed),
            (r"(previsao|\bclima\b|chover|\bchuva\b|\bfrio\b|\bcalor\b)", self._weather),
            (r"\b(processos|consumindo|pesando)\b", self._procs),
            (r"\b(temperatura|quente|aquecendo)", self._temp),
            (r"\b(cpu|processador)\b", self._cpu),
            (r"\b(memoria|ram)\b", self._mem),
            (r"\b(disco|armazenamento|espaco|rom|ssd|hd)\b", self._disk),
            (r"\brelatorio\b", self._report),
            (r"^analis\w*\s+(?:a pasta |o diretorio |a )?(.+)$", self._analyze),
            (r"^(?:anot|lembr|salv)\w*\s+(?:que |uma nota:? |nota:? )?(.+)$", self._note),
            (r"(minhas notas|ler notas|leia (as |minhas )?notas)", self._read_notes),
            (r"^(?:pesquis|procur|busc)\w*\s+(?:por |sobre )?(.+?)(?: no (youtube|google|wikipedia))?$", self._search),
            (r"^(?:abr|inici|execut|lanc)\w*\s+(?:o |a |os |as )?(.+)$", self._open),
        ]
        self.rules = [(re.compile(p), h) for p, h in self.rules]  # type: ignore[misc]

    # ------------------------------------------------------------------ roteamento
    def respond(self, history: list[dict[str, str]], executor: ToolExecutor) -> str:
        original = _clean(history[-1]["text"]) if history else ""
        norm = normalize(original)
        self.understood = True
        for pattern, handler in self.rules:
            m = pattern.search(norm)
            if m:
                return handler(m, original, executor)
        self.understood = False   # o assistente usa isso para explicar quando a IA online caiu
        return (f"Receio que no modo offline eu só entenda comandos diretos, {self.t}. "
                "Tente 'status do sistema', 'abra o spotify' ou 'toque Back in Black'. "
                "Para conversa livre, conecte um cérebro: Claude, GPT ou Gemini.")

    @staticmethod
    def _span(m: re.Match, original: str, group: int = 1) -> str:
        return original[m.start(group):m.end(group)].strip()

    # ------------------------------------------------------------------ handlers
    def _greet(self, m, o, e) -> str:
        h = datetime.now().hour
        saud = "Bom dia" if h < 12 else "Boa tarde" if h < 18 else "Boa noite"
        return f"{saud}, {self.t}. Todos os sistemas operacionais. Em que posso ajudar?"

    def _about(self, m, o, e) -> str:
        return (f"Sou o JARVIS, {self.t}. Monitoro o hardware, controlo aplicativos, processos e o Spotify, "
                "e converso com você por voz ou texto.")

    def _tool(self, name: str, **fixed: Any) -> Handler:
        def h(m, o, e) -> str:
            r = e.run(name, fixed)
            return self._say(r)
        return h

    def _spotify(self, acao: str) -> Handler:
        return self._tool("spotify_controle", acao=acao)

    def _media(self, acao: str) -> Handler:
        return self._tool("controle_midia", acao=acao)

    def _say(self, r: dict[str, Any]) -> str:
        if r.get("status") == "aguardando_confirmacao":
            return f"{r['pergunta']} Aguardo sua confirmação, {self.t}."
        if r.get("ok") is False:
            return r.get("mensagem") or r.get("erro") or "Não foi possível concluir."
        return r.get("mensagem") or f"Feito, {self.t}."

    def _now_playing(self, m, o, e) -> str:
        r = e.run("spotify_tocando_agora", {})
        if r.get("musica"):
            return f"Está tocando {r['musica']}, de {r['artistas']}."
        return self._say(r)

    def _volume(self, m, o, e) -> str:
        n = int(m.group(1))
        tool = "spotify_volume" if "spotify" in m.group(0) else "definir_volume"
        return self._say(e.run(tool, {"percentual": n}))

    def _play(self, m, o, e) -> str:
        consulta = self._span(m, o)
        n = normalize(o)
        tipo = "playlist" if "playlist" in n else "artista" if "artista" in n else "musica"
        return self._say(e.run("spotify_tocar", {"consulta": consulta, "tipo": tipo}))

    def _power(self, m, o, e) -> str:
        acao = "desligar" if m.group(1) == "deslig" else "reiniciar"
        minutos = int(m.group(2)) if m.group(2) else 1
        return self._say(e.run("agendar_desligamento", {"acao": acao, "minutos": minutos}))

    def _kill(self, m, o, e) -> str:
        return self._say(e.run("encerrar_processo", {"alvo": self._span(m, o)}))

    def _status(self, m, o, e) -> str:
        e.run("status_sistema", {})
        return f"Diagnóstico concluído, {self.t}: {sm.summary_text()}"

    def _temp(self, m, o, e) -> str:
        t = sm.temperature_info(wait=True)["cpu_c"]
        gpus = sm.gpu_info()
        parts = []
        if t is not None:
            parts.append(f"A CPU está a {t:.0f} graus")
        for g in gpus:
            parts.append(f"a GPU a {g['temp_c']:.0f} graus")
        if not parts:
            return ("Não consigo ler os sensores de temperatura deste sistema. No Windows, execute o "
                    "LibreHardwareMonitor ou rode o JARVIS como administrador.")
        return ", ".join(parts) + f", {self.t}."

    def _cpu(self, m, o, e) -> str:
        c = sm.cpu_info()
        return f"O processador está em {c['percent']:.0f} por cento, com {c['nucleos_logicos']} núcleos lógicos, {self.t}."

    def _mem(self, m, o, e) -> str:
        mem = sm.memory_info()
        alerta = " Recomendo fechar alguns programas." if mem["percent"] > 90 else ""
        return f"Memória em {mem['percent']:.0f} por cento: {mem['usado_gb']} de {mem['total_gb']} gigabytes.{alerta}"

    def _disk(self, m, o, e) -> str:
        ds = sm.disk_info()
        if not ds:
            return "Não encontrei discos montados."
        return " ".join(f"{d['ponto_montagem']} tem {d['livre_gb']:.0f} gigabytes livres de {d['total_gb']:.0f}." for d in ds[:3])

    def _procs(self, m, o, e) -> str:
        key = "memoria" if "memoria" in normalize(o) or "ram" in normalize(o) else "cpu"
        ps = e.run("listar_processos", {"ordenar_por": key, "limite": 3})
        lst = ps if isinstance(ps, list) else ps.get("resultado", [])
        desc = ", ".join(f"{p['nome']} ({p['memoria_mb']:.0f} MB)" if key == "memoria" else f"{p['nome']} ({p['cpu_percent']:.0f}%)"
                         for p in lst)
        return f"Os processos que mais consomem {'memória' if key == 'memoria' else 'CPU'} agora: {desc}."

    def _report(self, m, o, e) -> str:
        r = e.run("gerar_relatorio", {})
        return f"Relatório gerado em {r.get('arquivo')}, {self.t}." if r.get("ok") else self._say(r)

    def _analyze(self, m, o, e) -> str:
        r = e.run("analisar_diretorio", {"caminho": self._span(m, o)})
        if not r.get("ok"):
            return self._say(r)
        maior = r["maiores"][0] if r["maiores"] else None
        extra = f" O maior arquivo tem {maior['mb']:.0f} MB." if maior else ""
        return f"A pasta tem {r['arquivos']} arquivos somando {r['total_mb']:.0f} MB.{extra}"

    def _note(self, m, o, e) -> str:
        return self._say(e.run("salvar_nota", {"texto": self._span(m, o)}))

    def _read_notes(self, m, o, e) -> str:
        notas = e.run("ler_notas", {"limite": 3}).get("notas", [])
        return ("Suas últimas notas: " + " ".join(n.lstrip("- ") for n in notas)) if notas else "Você não tem notas salvas."

    def _search(self, m, o, e) -> str:
        consulta = self._span(m, o)
        if m.group(2) or normalize(o).startswith(("abr", "mostr")):   # "no youtube": abre no navegador
            return self._say(e.run("pesquisar_web", {"consulta": consulta, "site": m.group(2) or "google"}))
        r = e.run("buscar_web", {"consulta": consulta, "limite": 3})
        if not r.get("ok"):
            return self._say(r)
        top = r["resultados"][:2]
        partes = [f"{x['titulo']}: {x['resumo']}" if x["resumo"] else x["titulo"] for x in top]
        return f"Encontrei isto sobre {consulta}, {self.t}. " + " ".join(p.rstrip(".") + "." for p in partes)

    def _weather(self, m, o, e) -> str:
        # Cidade = o que vem depois do último "em/para/no/na", sem "hoje"/"amanhã"
        norm = normalize(o)
        cidade = ""
        for c in re.finditer(r"\b(?:em|para|no|na)\s+(?!hoje\b|amanha\b)([a-z][a-z .'-]*)", norm):
            cidade = o[c.start(1):c.end(1)]
        cidade = re.sub(r"\s*\b(hoje|amanh[ãa]|agora)\b.*$", "", cidade.strip(), flags=re.I).strip()
        r = e.run("previsao_tempo", {"cidade": cidade, "dias": 5})       # os cards mostram 5 dias
        if not r.get("ok"):
            return self._say(r)
        a, hoje = r["agora"], r["dias"][0]
        amanha = r["dias"][1] if len(r["dias"]) > 1 else None
        txt = (f"Em {r['local'].split(',')[0]} faz {a['temperatura_c']:.0f} graus agora, {a['condicao']}. "
               f"Hoje a mínima é {hoje['min_c']:.0f} e a máxima {hoje['max_c']:.0f}, "
               f"com {hoje['chance_chuva_pct'] or 0} por cento de chance de chuva.")
        if amanha and "amanha" in normalize(o):
            txt = (f"Amanhã em {r['local'].split(',')[0]}: {amanha['condicao']}, entre {amanha['min_c']:.0f} e "
                   f"{amanha['max_c']:.0f} graus, com {amanha['chance_chuva_pct'] or 0} por cento de chance de chuva.")
        if (hoje["chance_chuva_pct"] or 0) >= 50 and "amanha" not in normalize(o):
            txt += f" Eu levaria um guarda-chuva, {self.t}."
        return txt

    def _massivas(self, m, o, e) -> str:
        norm = normalize(o)
        lugar = ""
        achado = re.search(r"\b(?:em|no|na|de|para)\s+([a-z0-9][a-z0-9 .'-]*)$", norm)
        if achado and not re.fullmatch(r"(aberto|aberta|andamento|algum lugar|geral|hoje|agora)", achado.group(1).strip()):
            lugar = o[achado.start(1):achado.end(1)].strip()
        r = e.run("consultar_massivas", {"local": lugar})
        if not r.get("ok"):
            return self._say(r)
        onde = f" em {lugar}" if lugar else ""
        if not r["encontradas"]:
            txt = f"Não há massiva ativa{onde} no momento, {self.t}."
        else:
            partes = []
            for x in r["massivas"][:3]:
                if x["tipo"].startswith("queda PPPoE"):
                    partes.append(f"queda de PPPoE no concentrador {x['concentrador']} com {x['afetados']} afetados")
                else:
                    partes.append(f"{x['cidade']}, {x['status'].lower()}, {x['afetados']} afetados")
            txt = (f"Sim, {self.t}: {r['encontradas']} massiva(s){onde}. " + "; ".join(partes) + ".")
        if r.get("aviso"):
            txt += " Atenção: " + r["aviso"]
        return txt

    def _news(self, m, o, e) -> str:
        tema = re.search(r"\bnoticias? (?:sobre|de|do|da) (.+)$", normalize(o))
        r = e.run("noticias", {"tema": o[tema.start(1):tema.end(1)].strip() if tema else "", "limite": 8})
        if not r.get("ok"):
            return self._say(r)
        manchetes = "; ".join(f"{n['titulo']}, pelo {n['fonte']}" if n["fonte"] else n["titulo"]
                             for n in r["noticias"][:4])        # fala 4; os cards mostram todas
        return f"As principais de agora, {self.t}: {manchetes}."

    def _work(self, m, o, e) -> str:
        r = e.run("atualizacoes_trabalho", {})
        mv = r.get("massivas") or {}
        if mv.get("ok"):
            n = mv["total_ativas_no_geral"]
            if n:
                maiores = [str(x.get("cidade") or x.get("concentrador")) for x in mv["massivas"][:3]]
                txt = (f"No trabalho, {self.t}: {n} massiva(s) em aberto, com {mv['total_afetados_encontrados']} "
                       f"afetados; as maiores em {', '.join(maiores)}.")
            else:
                txt = f"No trabalho, {self.t}: nenhuma massiva em aberto."
            if mv.get("avisos_n2"):
                txt += f" Há {len(mv['avisos_n2'])} aviso(s) N2 ativo(s)."
            if mv.get("aviso"):
                txt += " Atenção: " + mv["aviso"]
        else:
            txt = mv.get("mensagem", "Não consegui ler as massivas.")
        sr = r.get("status_report")
        if isinstance(sr, dict):
            txt += f" O último status report é de {sr['gerado_em']}" + ("" if sr["de_hoje"] else "; o de hoje ainda não saiu") + "."
        else:
            txt += " Ainda não há status report salvo."
        return txt

    def _robot(self, m, o, e) -> str:
        n = normalize(o)
        if re.search(r"\b(ultimo|le|leia|ler|como (foi|esta|ficou)|resultado|mostra)\b", n):
            r = e.run("status_robot", {"acao": "ultimo"})
            if not r.get("ok"):
                return self._say(r)
            c = r.get("campos") or {}
            resumo = (f" Fila digital {c['filaDigital']}, backlog {c['backlog']}, {c['totalEventosAtivos']} eventos."
                      if all(k in c for k in ("filaDigital", "backlog", "totalEventosAtivos")) else "")
            return f"O último status report é de {r['gerado_em']}, {self.t}.{resumo}"
        extras: dict[str, str] = {}
        faltas = re.search(r"\b(\d+)\s+faltas?\b|\bfaltas?\s*(?:de |foram |=|:)?\s*(\d+)\b", n)
        if faltas:
            extras["faltas"] = faltas.group(1) or faltas.group(2)
        elif re.search(r"\b(sem|nenhuma|zero) faltas?\b", n):
            extras["faltas"] = "0"
        for bot, chave in (("telefonico", "bot_telefonico"), ("digital", "bot_digital")):
            if re.search(rf"\bbot {bot} (falhou|caiu|fora|com erro|nao)", n):
                extras[chave] = "falhou"
        if re.search(r"\bprogram", n):
            sem_faltas = re.sub(r"\b\d+\s+faltas?\b|\bfaltas?\s*\D{0,8}\d+", "", n)
            hora = re.search(r"\b(\d{1,2})(?:[:h](\d{2}))?\s*(?:h|horas)?\b", sem_faltas)
            horario = f"{int(hora.group(1)):02d}:{hora.group(2) or '00'}" if hora else ""
            return self._say(e.run("status_robot", {"acao": "programar", "horario": horario, **extras}))
        return self._say(e.run("status_robot", {"acao": "gerar", **extras}))

    def _cards(self, acao: str) -> Handler:
        def handler(m, o, e) -> str:
            args = {"acao": acao, "card": self._span(m, o, 1)}
            if acao == "mover":
                args["zona"] = self._span(m, o, 2)
                if m.group(3):
                    args["posicao"] = "topo" if m.group(3) in ("topo", "cima", "inicio") else "fim"
            return self._say(e.run("controlar_cards", args))
        return handler

    def _whatsapp(self, m, o, e) -> str:
        r = e.run("whatsapp_enviar", {"contato": self._span(m, o, 1), "mensagem": self._span(m, o, 2)})
        if r.get("status") == "aguardando_confirmacao":
            return f"{r['pergunta']} Diga sim para enviar, {self.t}."
        return self._say(r)

    def _whatsapp_salvar(self, m, o, e) -> str:
        return self._say(e.run("whatsapp_contatos", {"acao": "salvar", "nome": self._span(m, o, 1),
                                                      "numero": self._span(m, o, 2)}))

    def _any_music(self, m, o, e) -> str:
        r = e.run("spotify_tocar", {})
        return f"Retomando sua música, {self.t}." if r.get("ok") else self._say(r)

    def _speed(self, m, o, e) -> str:
        r = e.run("teste_velocidade", {})
        if not r.get("ok"):
            return self._say(r)
        return (f"Sua conexão está com {r['download_mbps']:.0f} megabits de download, {r['upload_mbps']:.0f} de upload "
                f"e latência de {r['latencia_ms']} milissegundos, {self.t}.")

    def _open(self, m, o, e) -> str:
        alvo = self._span(m, o)
        if re.fullmatch(r"[\w.-]+\.[a-z]{2,}(/\S*)?", alvo.lower()):
            return self._say(e.run("abrir_site", {"url": alvo}))
        return self._say(e.run("abrir_aplicativo", {"nome": alvo}))
