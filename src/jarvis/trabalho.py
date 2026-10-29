"""Ferramentas do trabalho: StatusRobotReport (status report da central) e o resumo "atualizações do trabalho".

O StatusRobotReport é um programa de console interativo (Playwright + Chrome) que coleta os números
do atendimento/ERP e salva o relatório em Documentos\\Relatorios MHNET\\relatorio-AAAA-MM-DD.txt/.json.
O JARVIS abre o robô numa janela própria e responde sozinho às perguntas que ele sabe (modo, logins
salvos, eventos/afetados das massivas, bots, faltas/horário se você disser) — ver console_input.py.
Logins, código do e-mail e o que ele não souber continuam com você, na janela do robô.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import massivas
from .logger import log

IS_WINDOWS = os.name == "nt"
EXE_NOMES = ("statusrobotreport.exe",)
PROCESSOS = {"statusrobotreport.exe", "statusrobot.exe"}
PULAR = {"node_modules", ".git", ".venv", "venv", "__pycache__", "site-packages", "perfil-navegador", "build",
         "lib", "include", "appdata", "$recycle.bin", "windows", "program files"}
_cache: dict[str, Path | None] = {}


def pasta_relatorios() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents" / "Relatorios MHNET"


def _raizes() -> list[Path]:
    casa = Path(os.environ.get("USERPROFILE") or Path.home())
    raizes = [casa / "Desktop", casa / "Documents", casa / "Downloads", casa / "OneDrive" / "Desktop",
              casa / "OneDrive" / "Documents", casa / "OneDrive" / "Área de Trabalho"]
    for var in ("APPDATA", "LOCALAPPDATA"):
        if os.environ.get(var):
            raizes.append(Path(os.environ[var]) / "Programs")
    return [r for r in raizes if r.is_dir()]


def _procurar(raiz: Path, profundidade: int = 6) -> list[Path]:
    achados: list[Path] = []
    base = len(raiz.parts)
    for pasta, subpastas, arquivos in os.walk(raiz):
        if len(Path(pasta).parts) - base >= profundidade:
            subpastas[:] = []
        subpastas[:] = [d for d in subpastas if d.lower() not in PULAR and not d.startswith(".")]
        achados += [Path(pasta) / a for a in arquivos if a.lower() in EXE_NOMES]
    return achados


def localizar(forcar: bool = False) -> Path | None:
    """Acha o StatusRobotReport.exe (JARVIS_STATUSROBOT tem prioridade); o mais recente vence."""
    manual = os.environ.get("JARVIS_STATUSROBOT", "").strip().strip('"')
    if manual:
        return Path(manual) if Path(manual).is_file() else None
    if not forcar and "exe" in _cache and (_cache["exe"] is None or _cache["exe"].is_file()):
        return _cache["exe"]
    achados = [p for r in _raizes() for p in _procurar(r)]
    # A cópia em "dist" de uma pasta de build é igual à de "outputs"; qualquer uma serve, prefere a mais nova
    _cache["exe"] = max(achados, key=lambda p: p.stat().st_mtime) if achados else None
    return _cache["exe"]


def rodando() -> bool:
    try:
        import psutil
    except ImportError:
        return False
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() in PROCESSOS:
            return True
    return False


def ultimo_relatorio() -> dict[str, Any]:
    pasta = pasta_relatorios()
    arquivos = sorted(pasta.glob("relatorio-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) if pasta.is_dir() else []
    if not arquivos:
        return {"ok": False, "mensagem": "Ainda não há nenhum relatório do StatusRobot salvo em "
                                         f"{pasta}. Peça 'gera o status report' para eu abrir o robô."}
    txt = arquivos[0]
    gerado = datetime.fromtimestamp(txt.stat().st_mtime)
    r: dict[str, Any] = {"ok": True, "arquivo": str(txt), "gerado_em": gerado.strftime("%d/%m/%Y %H:%M"),
                         "de_hoje": gerado.date() == datetime.now().date(),
                         "texto": re.sub(r"\*", "", txt.read_text(encoding="utf-8", errors="replace"))[:3500]}
    dados = txt.with_suffix(".json")
    if dados.is_file():
        try:
            r["campos"] = {k: v for k, v in json.loads(dados.read_text(encoding="utf-8")).items() if v not in ("", None)}
        except (OSError, json.JSONDecodeError):
            pass
    return r


# Perguntas do StatusRobotReport (input() do robô) -> padrão da linha do console
P_MODO = r"Escolha o modo:\s*$"
P_HORARIO = r"Horario para iniciar o report \(HH:MM\):\s*$"
P_LOGIN = r"^Opcao:\s*$"
P_FALTAS = r"Faltas do dia:\s*$"
P_EVENTOS = r"Total de Eventos Ativos:\s*$"
P_AFETADOS = r"Estimativa de Clientes Afetados:\s*$"
P_BOT_TEL = r"Bot Telefonico \[.*\]:\s*$"
P_BOT_DIG = r"Bot Digital \[.*\]:\s*$"


def _icone(valor: str) -> str:
    """'' (Enter) = ✅ no robô; 'falhou', 'não', 'erro', '❌'... = ❌."""
    return "❌" if re.search(r"(❌|falh|n[aã]o|erro|caiu|fora)", valor or "", re.I) else ""


def respostas(programado: bool, horario: str = "", faltas: str = "", bot_telefonico: str = "",
              bot_digital: str = "") -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Regras de preenchimento e o que foi preenchido (para o JARVIS contar ao usuário)."""
    regras: list[tuple[str, str]] = []
    feito: dict[str, Any] = {}
    if not programado:
        regras.append((P_MODO, "1"))
    regras.append((P_LOGIN, ""))                      # Enter = usar os logins salvos
    if programado and re.fullmatch(r"\d{1,2}:\d{2}", horario or ""):
        regras.append((P_HORARIO, horario))
        feito["horario"] = horario
    if str(faltas or "").strip():
        regras.append((P_FALTAS, str(faltas).strip()))
        feito["faltas"] = str(faltas).strip()
    numeros = massivas.para_report()
    if numeros is not None:
        regras += [(P_EVENTOS, str(numeros["eventos"])), (P_AFETADOS, str(numeros["afetados"]))]
        feito.update(numeros)
    tel, dig = _icone(bot_telefonico), _icone(bot_digital)
    regras += [(P_BOT_TEL, tel), (P_BOT_DIG, dig)]
    feito["bot_telefonico"], feito["bot_digital"] = tel or "✅", dig or "✅"
    return regras, feito


def _iniciar_preenchimento(pid: int, regras: list[tuple[str, str]]) -> None:
    """Sobe o "digitador" (console_input) num processo sem console, grudado no console do robô."""
    import sys
    dados = json.dumps(regras, ensure_ascii=False)
    if getattr(sys, "frozen", False):
        cmd, env = [sys.executable, "--preencher-console", str(pid), dados], None
    else:
        cmd = [sys.executable, "-m", "jarvis.console_input", str(pid), dados]
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    subprocess.Popen(cmd, env=env, creationflags=0x00000008)        # DETACHED_PROCESS


def executar(modo: str = "agora", horario: str = "", faltas: str = "", bot_telefonico: str = "",
             bot_digital: str = "") -> dict[str, Any]:
    """Abre o StatusRobotReport num console próprio e preenche sozinho o que o JARVIS sabe."""
    if not IS_WINDOWS:
        return {"ok": False, "mensagem": "O StatusRobotReport só roda no Windows."}
    if rodando():
        return {"ok": True, "mensagem": "O StatusRobot já está aberto; continue pela janela dele."}
    exe = localizar()
    if exe is None:
        return {"ok": False, "mensagem": "Não encontrei o StatusRobotReport.exe neste PC (procurei em Área de "
                                         "Trabalho, Documentos e Downloads). Coloque-o numa dessas pastas ou defina "
                                         "JARVIS_STATUSROBOT com o caminho do arquivo."}
    programado = modo == "programado" or bool(horario)
    regras, feito = respostas(programado, horario, faltas, bot_telefonico, bot_digital)
    args = [str(exe)] + (["--scheduled-report"] if programado else [])
    proc = subprocess.Popen(args, cwd=str(exe.parent), creationflags=subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]
    _iniciar_preenchimento(proc.pid, regras)
    import threading
    threading.Thread(target=_vigiar_report, args=(time.time(),), daemon=True, name="vigia-report").start()
    log.info("StatusRobotReport aberto (%s), preenchendo: %s", "programado" if programado else "agora", feito)

    partes = ["modo programado" if programado else "report agora", "logins salvos"]
    if "horario" in feito:
        partes.append(f"horário {feito['horario']}")
    if "eventos" in feito:
        partes.append(f"{feito['eventos']} eventos e {feito['afetados']} afetados (só massivas, sem o Grafana)")
    if "faltas" in feito:
        partes.append(f"{feito['faltas']} faltas")
    partes.append(f"bot telefônico {feito['bot_telefonico']} e bot digital {feito['bot_digital']}")
    falta = []
    if programado and "horario" not in feito:
        falta.append("o horário")
    if "faltas" not in feito:
        falta.append("as faltas do dia")
    if "eventos" not in feito:
        falta.append("eventos e afetados (o MHnet Notifier não está neste PC)")
    msg = "Abri o StatusRobotReport e estou preenchendo: " + ", ".join(partes) + "."
    if falta:
        msg += " Só falta você digitar na janela dele: " + " e ".join(falta) + "."
    if massivas.consultar().get("aviso"):
        msg += " Atenção: a lista do Notifier está desatualizada, confira os números."
    return {"ok": True, "mensagem": msg, "preenchido": feito, "falta_digitar": falta, "programa": str(exe)}


def status_robot(acao: str = "gerar", horario: str = "", faltas: str = "", bot_telefonico: str = "",
                 bot_digital: str = "") -> dict[str, Any]:
    acao = (acao or "gerar").lower()
    if acao in ("ultimo", "ler", "resultado"):
        return ultimo_relatorio()
    if acao == "status":
        exe = localizar()
        ult = ultimo_relatorio()
        return {"ok": True, "instalado": bool(exe), "programa": str(exe) if exe else None, "rodando": rodando(),
                "ultimo_relatorio": ult.get("gerado_em"), "ultimo_e_de_hoje": ult.get("de_hoje", False)}
    return executar("programado" if acao == "programar" else "agora", horario, faltas, bot_telefonico, bot_digital)


# --------------------------------------------------------------------------- status report -> WhatsApp
def texto_relatorio() -> dict[str, Any] | None:
    """Último relatório exatamente como o robô montou (com a formatação do WhatsApp: *negrito*)."""
    pasta = pasta_relatorios()
    arquivos = sorted(pasta.glob("relatorio-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True) if pasta.is_dir() else []
    if not arquivos:
        return None
    gerado = datetime.fromtimestamp(arquivos[0].stat().st_mtime)
    return {"texto": arquivos[0].read_text(encoding="utf-8", errors="replace").strip(), "arquivo": str(arquivos[0]),
            "gerado_em": gerado.strftime("%d/%m às %H:%M"), "de_hoje": gerado.date() == datetime.now().date()}


def confirmacao_report(args: dict[str, Any]) -> str:
    """Pré-visualização: termina com '?' para pedir confirmação, ou explica o que falta."""
    rel = texto_relatorio()
    if rel is None:
        return "Ainda não há status report salvo; peça \"gera o status report\" primeiro."
    from .config import settings
    grupo = " ".join(str(args.get("grupo") or settings.report_grupo or "").split())
    if not grupo:
        return "Para qual grupo do WhatsApp devo mandar o status report? Diga, por exemplo, \"manda o report no grupo Supervisão\"."
    primeira = rel["texto"].splitlines()[0].replace("*", "").strip()[:60]
    aviso = "" if rel["de_hoje"] else " Atenção: é o de " + rel["gerado_em"].split(" ")[0] + "; o de hoje ainda não saiu."
    return f"Status report de {rel['gerado_em']} (começa com “{primeira}”).{aviso} Enviar no WhatsApp para “{grupo}”?"


def enviar_report(grupo: str = "") -> dict[str, Any]:
    from . import whatsapp
    from .config import settings
    rel = texto_relatorio()
    grupo = " ".join((grupo or settings.report_grupo or "").split())
    if rel is None or not grupo:
        return {"ok": False, "mensagem": confirmacao_report({"grupo": grupo})}
    r = whatsapp.enviar(grupo, rel["texto"])
    if r.get("ok") and r.get("contato") and r["contato"] != settings.report_grupo:
        settings.set_vars({"STATUS_REPORT_GRUPO": r["contato"]})          # da próxima vez, é só "manda o report"
    if r.get("ok"):
        r["mensagem"] = f"Status report enviado para {r.get('contato', grupo)}" + (" (conferido)." if r.get("conferido") else ".")
    return r


# Avisa quando o StatusRobot termina (o robô grava relatorio-AAAA-MM-DD.txt no fim)
ao_ficar_pronto: Any = None            # o servidor liga aqui a função que oferece o envio


def _vigiar_report(desde: float, limite_h: float = 4) -> None:
    pasta = pasta_relatorios()
    fim = time.time() + limite_h * 3600
    while time.time() < fim:
        time.sleep(15)
        novos = [p for p in pasta.glob("relatorio-*.txt") if p.stat().st_mtime > desde] if pasta.is_dir() else []
        if novos:
            time.sleep(5)                                   # deixa o robô terminar de gravar
            log.info("Status report pronto: %s", novos[0])
            if ao_ficar_pronto:
                try:
                    ao_ficar_pronto()
                except Exception:
                    log.exception("Falha ao oferecer o envio do status report")
            return
        if not rodando() and time.time() - desde > 120:     # robô fechado sem gerar relatório
            return


def atualizacoes() -> dict[str, Any]:
    """Resumo do trabalho: massivas em aberto, avisos N2 e o status report do dia."""
    m = massivas.consultar(incluir_avisos=True)
    r: dict[str, Any] = {"ok": True, "massivas": m}
    ult = ultimo_relatorio()
    if ult.get("ok"):
        r["status_report"] = {"gerado_em": ult["gerado_em"], "de_hoje": ult["de_hoje"],
                              "campos": ult.get("campos") or ult["texto"][:1500]}
    else:
        r["status_report"] = "nenhum relatório salvo ainda"
    r["status_robot_aberto"] = rodando()
    return r
