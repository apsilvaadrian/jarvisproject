"""Registro de informações operacionais.

- logs/jarvis.log   -> log textual do processo (rotacionado por tamanho)
- logs/acoes.jsonl  -> uma linha JSON por ação executada no sistema (auditoria)
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any

from .config import settings

_lock = threading.Lock()


def setup_logging() -> logging.Logger:
    settings.ensure_dirs()
    logger = logging.getLogger("jarvis")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        settings.log_dir / "jarvis.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    if sys.stderr is not None:          # no .exe sem console não existe stderr
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        logger.addHandler(console)
    return logger


log = setup_logging()


def log_action(tool: str, args: dict[str, Any], ok: bool, result: Any, origem: str = "") -> None:
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "ferramenta": tool,
        "argumentos": args,
        "sucesso": ok,
        "origem": origem,
        "resultado": result if isinstance(result, (str, int, float, bool)) else str(result)[:500],
    }
    with _lock:
        with open(settings.log_dir / "acoes.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log.info("acao=%s ok=%s args=%s", tool, ok, args)


def recent_actions(limit: int = 20) -> list[dict[str, Any]]:
    path = settings.log_dir / "acoes.jsonl"
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))
