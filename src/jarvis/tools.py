"""Ferramentas que o "cérebro" (LLM ou offline) pode chamar.

Cada ferramenta tem um JSON Schema (convertido para o formato de cada provedor)
e um handler Python. Ferramentas com confirm=True não executam na hora: geram um
pedido de confirmação que o usuário aprova/recusa na interface ou por voz.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from . import apps, avisos, cards, embutidos, integracoes, letras, massivas, os_control, report, spotify, trabalho, web_tools, whatsapp
from . import system_monitor as sm
from .logger import log, log_action


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    confirm: bool = False
    confirm_text: Callable[[dict[str, Any]], str] | None = None


def _obj(props: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props or {}, "required": required or []}


def _s(desc: str, enum: list[str] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"type": "string", "description": desc}
    if enum:
        d["enum"] = enum
    return d


def _i(desc: str) -> dict[str, Any]:
    return {"type": "integer", "description": desc}


def _kill_text(a: dict[str, Any]) -> str:
    p = os_control.describe_kill(a.get("alvo", ""))
    if not p["ok"]:
        return p["mensagem"]
    return f"Encerrar {p['quantidade']} processo(s) '{p['nome']}' (PIDs {p['pids'][:5]}, {p['memoria_mb']} MB)?"


TOOLS: list[Tool] = [
    Tool("status_sistema", "Resumo completo do PC: CPU, RAM, discos, rede, temperatura, GPU, bateria e uptime.",
         _obj(), lambda: sm.snapshot(include_processes=False)),
    Tool("listar_processos", "Lista processos em execução ordenados por uso de CPU ou memória, com filtro opcional por nome.",
         _obj({"ordenar_por": _s("cpu ou memoria", ["cpu", "memoria"]),
               "limite": _i("quantidade de processos (padrão 10)"),
               "filtro": _s("parte do nome do processo")}),
         lambda ordenar_por="cpu", limite=10, filtro="": sm.list_processes(ordenar_por, limite, filtro)),
    Tool("encerrar_processo", "Encerra um processo pelo nome (ex.: chrome) ou PID. Exige confirmação do usuário.",
         _obj({"alvo": _s("nome do processo ou PID")}, ["alvo"]),
         lambda alvo: os_control.kill_process(alvo), confirm=True, confirm_text=_kill_text),
    Tool("abrir_aplicativo", "Abre um aplicativo instalado no PC (ex.: spotify, chrome, calculadora, vscode, discord, steam).",
         _obj({"nome": _s("nome do aplicativo")}, ["nome"]), lambda nome: os_control.open_app(nome)),
    Tool("abrir_site", "Abre um site no navegador padrão.",
         _obj({"url": _s("endereço, ex.: github.com")}, ["url"]), lambda url: os_control.open_website(url)),
    Tool("buscar_web", "Pesquisa na internet e DEVOLVE os resultados (títulos, links e resumos) para você responder "
                       "ao usuário. Use para notícias, fatos atuais, preços, horários, qualquer dúvida de informação.",
         _obj({"consulta": _s("o que pesquisar"), "limite": _i("quantos resultados (padrão 5)")}, ["consulta"]),
         lambda consulta, limite=5: web_tools.buscar_web(consulta, limite)),
    Tool("ler_pagina", "Lê o texto principal de uma página da web (use depois de buscar_web para obter detalhes).",
         _obj({"url": _s("endereço da página")}, ["url"]), lambda url: web_tools.ler_pagina(url)),
    Tool("previsao_tempo", "Clima atual e previsão dos próximos dias. Se o usuário não disser a cidade, chame SEM "
                           "cidade (o JARVIS usa a cidade dele automaticamente); não pergunte a cidade.",
         _obj({"cidade": _s("cidade, ex.: 'Campinas' (opcional)"), "dias": _i("dias de previsão, 1 a 7 (padrão 3)")}),
         lambda cidade="", dias=3: web_tools.previsao_tempo(cidade, dias)),
    Tool("consultar_massivas", "Massivas do trabalho (quedas de internet/OLT/PPPoE) sincronizadas pelo MHnet Notifier. "
                               "Use quando o usuário perguntar se tem massiva/queda em algum lugar. 'local' aceita "
                               "cidade, sigla do concentrador ou OLT; vazio lista todas as ativas.",
         _obj({"local": _s("cidade, concentrador ou OLT, ex.: 'Ponta Grossa', 'PGO' (opcional)"),
               "incluir_resolvidas": {"type": "boolean", "description": "incluir as já resolvidas"},
               "incluir_avisos": {"type": "boolean", "description": "incluir os Avisos N2 ativos"}}),
         lambda local="", incluir_resolvidas=False, incluir_avisos=False:
             massivas.consultar(local, incluir_resolvidas, incluir_avisos)),
    Tool("atualizacoes_trabalho", "Resumo do trabalho do usuário: massivas em aberto (MHnet Notifier), Avisos N2 e o "
                                  "status report do dia (StatusRobot). Use para 'atualizações do trabalho', 'como está "
                                  "o trabalho', 'resumo do trabalho'.",
         _obj(), lambda: trabalho.atualizacoes()),
    Tool("status_robot", "StatusRobotReport, o robô que monta o status report da central de atendimento. "
                         "acao='gerar' abre o robô e o JARVIS PREENCHE sozinho modo, logins salvos, eventos e "
                         "afetados (só massivas da planilha; Grafana não conta) e bots; 'programar' abre no modo "
                         "programado (passe horario HH:MM); 'ultimo' lê o último relatório; 'status' diz se está "
                         "instalado/aberto. Passe faltas se o usuário disser quantas foram.",
         _obj({"acao": _s("o que fazer", ["gerar", "programar", "ultimo", "status"]),
               "horario": _s("horário HH:MM para o modo programado (opcional)"),
               "faltas": _s("faltas do dia, se o usuário informar (opcional)"),
               "bot_telefonico": _s("só se o usuário disser que o bot telefônico falhou: 'falhou'"),
               "bot_digital": _s("só se o usuário disser que o bot digital falhou: 'falhou'")}),
         lambda acao="gerar", horario="", faltas="", bot_telefonico="", bot_digital="":
             trabalho.status_robot(acao, horario, faltas, bot_telefonico, bot_digital)),
    Tool("noticias", "Principais notícias do dia no Brasil (Google Notícias), ou sobre um tema. Use para 'quais as "
                     "notícias', 'me fala as notícias de hoje', 'notícias sobre X'.",
         _obj({"tema": _s("tema opcional, ex.: 'economia', 'futebol', 'Curitiba'"),
               "limite": _i("quantas manchetes (padrão 6)")}),
         lambda tema="", limite=6: web_tools.noticias(tema, limite)),
    Tool("controlar_cards", "Controla os cards (painéis) da interface do JARVIS: focar (traz o card para o centro da "
                            "tela, maior, como a letra da música) e desfocar (devolve ao lugar), fixar (fica no topo e não some), "
                            "desafixar, mover para outra coluna/posição, ocultar, mostrar, restaurar o layout padrão "
                            "ou listar onde está cada um. Colunas: esquerda, direita, extra (a última, no canto direito).",
         _obj({"acao": _s("o que fazer", ["focar", "desfocar", "fixar", "desafixar", "mover", "ocultar", "mostrar", "restaurar", "listar"]),
               "card": _s("nome do card, ex.: 'spotify', 'processos', 'agenda', 'alertas', 'rede', 'memória'"),
               "zona": _s("coluna de destino ao mover", ["esquerda", "direita", "extra"]),
               "posicao": _s("posição na coluna: 'topo', 'fim' ou um número (1 = primeiro)")}, ["acao"]),
         lambda acao, card="", zona="", posicao="": cards.controlar(acao, card, zona, posicao)),
    Tool("mostrar_card", "Mostra um card PERSONALIZADO na tela do JARVIS com dados estruturados: tabelas "
                         "(classificação/standings, placares, cotações, comparações), listas ou números. Use SEMPRE que a "
                         "resposta tiver dados em forma de tabela, ranking ou lista (ex.: classificação da Fórmula 1, "
                         "tabela do Brasileirão, preços). Busque os dados antes (buscar_web e ler_pagina); nunca invente "
                         "números. Envie TODAS as linhas encontradas (a tabela inteira, não só o top 5). Pode chamar mais de "
                         "uma vez para mostrar vários cards lado a lado.",
         _obj({"titulo": _s("título do card, ex.: 'Fórmula 1 — Pilotos 2026'"),
               "tipo": _s("formato", ["tabela", "lista", "numeros", "texto"]),
               "colunas": {"type": "array", "items": {"type": "string"}, "description": "tabela: nomes das colunas"},
               "linhas": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                          "description": "tabela: uma lista de células por linha, na ordem das colunas"},
               "itens": {"type": "array", "items": {"type": "object", "properties": {
                   "titulo": {"type": "string"}, "detalhe": {"type": "string"}, "valor": {"type": "string"}}},
                   "description": "lista ou numeros: itens com titulo, detalhe (opcional) e valor (opcional)"},
               "texto": _s("tipo texto: um parágrafo curto"),
               "fonte": _s("de onde vieram os dados, ex.: 'formula1.com'"),
               "destacar": _s("texto de uma linha/item para realçar (ex.: o piloto favorito do usuário)")},
              ["titulo", "tipo"]),
         lambda **kw: cards.personalizado(**kw)),
    Tool("whatsapp_enviar", "Envia uma mensagem no WhatsApp para um contato salvo (pelo nome) ou para um número. "
                            "Pede confirmação do usuário antes. Escreva a mensagem exatamente como ele pediu, em "
                            "primeira pessoa (é ele quem está mandando).",
         _obj({"contato": _s("nome do contato salvo ou número com DDD"), "mensagem": _s("texto a enviar")},
              ["contato", "mensagem"]),
         lambda contato, mensagem: whatsapp.enviar(contato, mensagem), confirm=True,
         confirm_text=whatsapp.texto_confirmacao),
    Tool("enviar_status_report", "Envia no WhatsApp o último status report gerado pelo StatusRobot (o texto exato, com "
                                 "a formatação) para um grupo. Sem grupo, usa o último grupo usado. Pede confirmação.",
         _obj({"grupo": _s("nome do grupo do WhatsApp (opcional: usa o último)")}),
         lambda grupo="": trabalho.enviar_report(grupo), confirm=True, confirm_text=trabalho.confirmacao_report),
    Tool("whatsapp_nao_lidas", "Lista as conversas do WhatsApp com mensagens NÃO LIDAS (quem mandou, prévia da última "
                               "mensagem, horário e quantas). Não marca nada como lido. Use para 'tenho mensagens?', "
                               "'quais mensagens pendentes', 'quem me mandou mensagem'.",
         _obj({"limite": _i("quantas conversas (padrão 10)")}), lambda limite=10: whatsapp.pendentes(limite)),
    Tool("whatsapp_ler_conversa", "Lê as últimas mensagens de uma conversa ou grupo do WhatsApp (abre a conversa, o que a "
                                  "marca como lida). Use para 'o que a Marini mandou', 'lê o grupo X'.",
         _obj({"contato": _s("nome do contato ou grupo"), "quantidade": _i("quantas mensagens (padrão 10)")}, ["contato"]),
         lambda contato, quantidade=10: whatsapp.ler(contato, quantidade)),
    Tool("whatsapp_contatos", "Contatos do WhatsApp que o JARVIS conhece: salvar (nome + número), remover ou listar.",
         _obj({"acao": _s("o que fazer", ["salvar", "remover", "listar"]), "nome": _s("nome do contato"),
               "numero": _s("número com DDD, ex.: 42 99999-0000")}, ["acao"]),
         lambda acao, nome="", numero="": whatsapp.contato(acao, nome, numero)),
    Tool("apps_janelas", "Lista as janelas/apps abertos no PC (título e programa).", _obj(), lambda: apps.listar()),
    Tool("app_ler", "Lê uma janela aberta: os botões, menus, abas, campos (com o texto deles) que ela mostra. Use antes de "
                    "clicar ou digitar para saber os nomes exatos.",
         _obj({"janela": _s("título ou programa, ex.: 'bloco de notas', 'calculadora', 'excel'"),
               "limite": _i("quantos controles (padrão 80)")}, ["janela"]),
         lambda janela, limite=80: apps.ler(janela, limite)),
    Tool("app_controlar", "Opera um app aberto: focar, minimizar, maximizar, restaurar, fechar a janela; clicar num "
                          "controle pelo nome (elemento); digitar texto (no campo 'elemento' ou onde estiver o cursor); "
                          "teclas de atalho (texto, ex.: 'ctrl+s', 'alt+tab', 'enter', 'f5'). Abra o app antes com "
                          "abrir_aplicativo e use app_ler para ver os nomes. Ações arriscadas pedem confirmação.",
         _obj({"acao": _s("o que fazer", ["focar", "minimizar", "maximizar", "restaurar", "fechar", "clicar", "digitar", "teclas"]),
               "janela": _s("título ou programa da janela"),
               "elemento": _s("nome do botão/menu/campo (para clicar ou digitar num campo)"),
               "texto": _s("texto para digitar ou teclas de atalho")}, ["acao", "janela"]),
         lambda acao, janela, elemento="", texto="": apps.controlar(acao, janela, elemento, texto),
         confirm=True, confirm_text=apps.confirmacao),
    Tool("abrir_app", "Abre o WhatsApp, o Instagram, o Facebook ou o YouTube DENTRO da interface do JARVIS para o usuário "
                      "usar ali mesmo: no centro (painel) ou num card ao lado dos outros (para deixar aberto enquanto faz "
                      "outra coisa, ex.: 'deixa o WhatsApp num card', 'abre o Instagram num card pra eu ver reels').",
         _obj({"app": _s("qual app", ["whatsapp", "instagram", "facebook", "youtube", "navegador"]),
               "onde": _s("no centro (painel) ou num card", ["painel", "card"])}, ["app"]),
         lambda app, onde="painel": embutidos.abrir_app(app, onde)),
    Tool("navegador_pesquisar", "Pesquisa no Google (ou abre um endereço) no Navegador do JARVIS, um navegador de verdade "
                                "que aparece no centro da tela. Use quando pedirem para pesquisar/procurar algo 'aqui', "
                                "'no navegador', 'no Google', ou para abrir um site completo (com login, vídeos...).",
         _obj({"consulta": _s("o que pesquisar no Google"), "url": _s("ou um endereço para abrir")}),
         lambda consulta="", url="": embutidos.navegador_pesquisar(consulta, url)),
    Tool("navegador_ler", "Lê o que está aberto agora no Navegador do JARVIS (título, endereço e texto da página): use "
                          "depois de navegador_pesquisar para responder com base no que apareceu, ou quando o usuário "
                          "perguntar sobre a página que ele está vendo ali.", _obj(), lambda: embutidos.navegador_ler()),
    Tool("apelido_contato", "Apelidos de contatos/grupos do WhatsApp: 'chama a Bia Souza Escritório SP de Bia'. Depois "
                            "disso o apelido funciona nos comandos e nos avisos. Ações: definir, remover, listar.",
         _obj({"acao": _s("o que fazer", ["definir", "remover", "listar"]),
               "apelido": _s("o apelido curto, ex.: 'Bia'"),
               "nome": _s("o nome como aparece no WhatsApp, ex.: 'Bia Souza Escritório SP'")}, ["acao"]),
         lambda acao, apelido="", nome="": avisos.apelido(acao, apelido, nome)),
    Tool("avisos_apps", "Liga/desliga os avisos de mensagens e notificações: de uma pessoa ou grupo do WhatsApp ('para de "
                        "me avisar da Bia', 'volta a avisar do grupo da família') ou de um app inteiro ('para de avisar do "
                        "Instagram'). Ações: silenciar, reativar, listar.",
         _obj({"acao": _s("o que fazer", ["silenciar", "reativar", "listar"]),
               "conversa": _s("pessoa, grupo ou apelido (vazio quando for o app inteiro)"),
               "app": _s("app inteiro", ["whatsapp", "instagram", "facebook"])}, ["acao"]),
         lambda acao, conversa="", app="": avisos.silenciar(acao, conversa, app)),
    Tool("abrir_pagina", "Abre uma página da internet (notícia, artigo, site) DENTRO da interface do JARVIS, no "
                         "navegador interno (modo leitura). Use quando pedirem para abrir/mostrar uma notícia ou site na "
                         "tela; para notícias, use o link que veio da ferramenta de notícias.",
         _obj({"url": _s("endereço da página")}, ["url"]),
         lambda url: {"ok": True, "url": url, "mensagem": "Página aberta na tela do JARVIS."}),
    Tool("mostrar_interface", "Traz a janela do JARVIS para o primeiro plano (restaura se estiver minimizada ou "
                              "na bandeja). Use quando o usuário pedir para acordar, aparecer ou mostrar a interface.",
         _obj(), lambda: _mostrar_interface()),
    Tool("agenda_eventos", "Compromissos do Google Agenda do usuário (hoje e próximos dias).",
         _obj({"dias": _i("quantos dias a partir de hoje (1 = só hoje, padrão 1)")}),
         lambda dias=1: integracoes.agenda(dias)),
    Tool("emails_nao_lidos", "E-mails não lidos da caixa de entrada (remetente, assunto, hora). Só leitura.",
         _obj({"limite": _i("quantos listar (padrão 5)")}), lambda limite=5: integracoes.email(limite)),
    Tool("github_resumo", "Resumo do GitHub do usuário: notificações, PRs dele, PRs aguardando revisão e repositórios recentes.",
         _obj(), lambda: integracoes.github()),
    Tool("docker_containers", "Lista os containers do Docker deste PC e o estado de cada um.", _obj(),
         lambda: integracoes.docker()),
    Tool("docker_controlar", "Inicia, para ou reinicia um container do Docker.",
         _obj({"container": _s("nome do container"), "acao": _s("ação", ["iniciar", "parar", "reiniciar"])},
              ["container", "acao"]),
         lambda container, acao: integracoes.docker_controlar(container, acao), confirm=True,
         confirm_text=lambda a: f"{str(a.get('acao', '')).capitalize()} o container {a.get('container')}?"),
    Tool("teste_velocidade", "Mede a velocidade da internet: download, upload e latência (leva cerca de 15 segundos).",
         _obj(), lambda: web_tools.teste_velocidade()),
    Tool("pesquisar_web", "ABRE uma pesquisa no navegador (google, youtube ou wikipedia). Só use quando o usuário "
                          "pedir para abrir/mostrar no navegador; para responder perguntas use buscar_web.",
         _obj({"consulta": _s("termo pesquisado"), "site": _s("onde pesquisar", ["google", "youtube", "wikipedia"])},
              ["consulta"]),
         lambda consulta, site="google": os_control.web_search(consulta, site)),
    Tool("controle_midia", "Teclas de mídia do sistema: tocar/pausar, próxima, anterior, parar, volume e mudo.",
         _obj({"acao": _s("ação", ["play_pause", "proxima", "anterior", "parar", "volume_mais", "volume_menos", "mudo"])},
              ["acao"]),
         lambda acao: os_control.media_control(acao)),
    Tool("definir_volume", "Define o volume geral do sistema em porcentagem (0 a 100).",
         _obj({"percentual": _i("0 a 100")}, ["percentual"]), lambda percentual: os_control.set_volume(percentual)),
    Tool("spotify_tocar", "Busca e toca no Spotify uma música, playlist, artista ou álbum. Sem consulta, retoma a reprodução.",
         _obj({"consulta": _s("o que tocar, ex.: 'Back in Black AC/DC'"),
               "tipo": _s("tipo de item", ["musica", "playlist", "artista", "album"])}),
         lambda consulta="", tipo="musica": spotify.play(consulta, tipo)),
    Tool("spotify_controle", "Controla a reprodução do Spotify.",
         _obj({"acao": _s("ação", ["pausar", "continuar", "proxima", "anterior", "aleatorio"])}, ["acao"]),
         lambda acao: spotify.control(acao)),
    Tool("spotify_tocando_agora", "Informa a música que está tocando no Spotify.", _obj(), lambda: spotify.now_playing()),
    Tool("mostrar_letra", "Mostra NA TELA a letra da música que está tocando no Spotify, acompanhando a música "
                          "(quadro de letra). Use quando pedirem a letra; acao='fechar' fecha o quadro. Não leia "
                          "a letra em voz alta.",
         _obj({"acao": _s("mostrar ou fechar", ["mostrar", "fechar"])}),
         lambda acao="mostrar": letras.mostrar(acao)),
    Tool("spotify_volume", "Define o volume do Spotify (0 a 100).",
         _obj({"percentual": _i("0 a 100")}, ["percentual"]), lambda percentual: spotify.volume(percentual)),
    Tool("analisar_diretorio", "Analisa uma pasta: espaço ocupado, maiores arquivos e uso por extensão.",
         _obj({"caminho": _s("caminho da pasta, ex.: ~/Downloads")}),
         lambda caminho="~": os_control.analyze_directory(caminho)),
    Tool("salvar_nota", "Salva uma anotação/lembrete do usuário em arquivo.",
         _obj({"texto": _s("conteúdo da nota")}, ["texto"]), lambda texto: os_control.save_note(texto)),
    Tool("ler_notas", "Lê as últimas anotações salvas.", _obj({"limite": _i("quantas notas")}),
         lambda limite=10: os_control.read_notes(limite)),
    Tool("gerar_relatorio", "Gera um relatório em arquivo .txt com o estado atual dos recursos do sistema.",
         _obj(), lambda: {"ok": True, "arquivo": str(report.generate_report())}),
    Tool("bloquear_tela", "Bloqueia a tela do computador.", _obj(), lambda: os_control.lock_screen()),
    Tool("agendar_desligamento", "Agenda desligar ou reiniciar o PC. Exige confirmação.",
         _obj({"acao": _s("desligar ou reiniciar", ["desligar", "reiniciar"]), "minutos": _i("atraso em minutos")},
              ["acao"]),
         lambda acao, minutos=1: os_control.schedule_power(acao, minutos), confirm=True,
         confirm_text=lambda a: f"{a.get('acao', 'desligar').capitalize()} o computador em {a.get('minutos', 1)} minuto(s)?"),
    Tool("cancelar_desligamento", "Cancela um desligamento/reinício agendado.", _obj(), lambda: os_control.cancel_power()),
    Tool("data_hora", "Data e hora atuais.", _obj(), lambda: {"agora": datetime.now().strftime("%A, %d/%m/%Y %H:%M:%S")}),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def _mostrar_interface() -> dict[str, Any]:
    from . import desktop, server
    gancho = server.ganchos.get("mostrar")        # no JARVIS.exe: mostra até a janela escondida na bandeja
    try:
        if gancho:
            gancho()
        elif not desktop.focus_window():
            return {"ok": False, "mensagem": "Não encontrei a janela do JARVIS para trazer à frente."}
    except OSError as exc:
        return {"ok": False, "mensagem": f"Não consegui trazer a janela à frente ({exc})."}
    return {"ok": True, "mensagem": "Interface em primeiro plano."}


# Ferramentas cujo resultado aparece em cards na interface (no lugar dos cards de hardware)
PAINEIS = {"atualizacoes_trabalho": "trabalho", "consultar_massivas": "trabalho", "noticias": "noticias",
           "previsao_tempo": "clima", "status_sistema": "sistema", "mostrar_letra": "letra",
           "controlar_cards": "cards", "mostrar_card": "custom", "abrir_pagina": "web", "abrir_app": "apps", "navegador_pesquisar": "apps",
           "whatsapp_nao_lidas": "custom", "whatsapp_ler_conversa": "custom", "agenda_eventos": "card:agenda", "emails_nao_lidos": "card:email",
           "github_resumo": "card:github", "docker_containers": "card:docker"}


class ToolExecutor:
    """Executa ferramentas para uma sessão e guarda as ações aguardando confirmação."""

    def __init__(self, emit: Callable[[dict[str, Any]], None] | None = None, origem: str = "chat"):
        self.emit = emit or (lambda _e: None)
        self.origem = origem
        self.pending: dict[str, tuple[Tool, dict[str, Any]]] = {}

    def run(self, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
        args = {k: v for k, v in (args or {}).items() if v is not None}
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return {"ok": False, "erro": f"Ferramenta desconhecida: {name}"}

        if tool.confirm:
            text = tool.confirm_text(args) if tool.confirm_text else f"Executar {name}?"
            if text is None:                                  # esta ação específica não precisa de confirmação
                return self._execute(tool, args)
            if text.endswith("?"):
                action_id = uuid.uuid4().hex[:8]
                self.pending[action_id] = (tool, args)
                self.emit({"type": "confirm_request", "id": action_id, "tool": name, "texto": text})
                return {"ok": True, "status": "aguardando_confirmacao", "pergunta": text,
                        "instrucao": "Peça ao usuário para confirmar na tela ou dizendo 'confirmar'."}
            return {"ok": False, "mensagem": text}  # recusado já na pré-visualização

        return self._execute(tool, args)

    def _execute(self, tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
        self.emit({"type": "action", "tool": tool.name, "args": args, "status": "executando"})
        try:
            result = tool.handler(**args)
            ok = not (isinstance(result, dict) and result.get("ok") is False)
        except TypeError as exc:
            result, ok = {"ok": False, "erro": f"Argumentos inválidos: {exc}"}, False
        except Exception as exc:  # nunca derruba a conversa por erro de ferramenta
            log.exception("Erro na ferramenta %s", tool.name)
            result, ok = {"ok": False, "erro": str(exc)}, False
        log_action(tool.name, args, ok, result.get("mensagem", "ok") if isinstance(result, dict) else "ok", self.origem)
        self.emit({"type": "action", "tool": tool.name, "args": args, "status": "ok" if ok else "erro",
                   "mensagem": result.get("mensagem") if isinstance(result, dict) else None})
        if ok and tool.name in PAINEIS and not (PAINEIS[tool.name] == "custom" and not result.get("card")):
            self.emit({"type": "painel", "tipo": PAINEIS[tool.name], "dados": result})
        return result if isinstance(result, dict) else {"ok": True, "resultado": result}

    def resolve(self, action_id: str, approve: bool) -> dict[str, Any] | None:
        item = self.pending.pop(action_id, None)
        if item is None:
            return None
        tool, args = item
        if not approve:
            log_action(tool.name, args, False, "recusado pelo usuário", self.origem)
            return {"ok": False, "mensagem": "Ação cancelada.", "tool": tool.name}
        result = self._execute(tool, args)
        result.setdefault("tool", tool.name)
        return result

    def resolve_latest(self, approve: bool) -> dict[str, Any] | None:
        if not self.pending:
            return None
        return self.resolve(next(reversed(self.pending)), approve)
