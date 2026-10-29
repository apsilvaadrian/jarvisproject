"""Ponto de entrada: python -m jarvis [servidor|cli|relatorio|status]."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import webbrowser


def _cli() -> None:
    from .assistant import Assistant

    def emit(e: dict) -> None:
        if e.get("type") == "confirm_request":
            print(f"  [confirmação] {e['texto']}  (responda 'sim' ou 'não')")
        elif e.get("type") == "warning":
            print(f"  [aviso] {e['mensagem']}")

    jarvis = Assistant(emit)
    print(f"J.A.R.V.I.S. — modo terminal (cérebro: {jarvis.brain_label}). Digite 'sair' para encerrar.")
    while True:
        try:
            text = input("\nVocê > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if text.lower() in ("sair", "exit", "quit"):
            break
        print("JARVIS >", jarvis.chat(text))


def main(argv: list[str] | None = None) -> int:
    # Garante acentos no console do Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. — assistente pessoal")
    parser.add_argument("modo", nargs="?", default="servidor", choices=["servidor", "cli", "relatorio", "status"])
    parser.add_argument("--no-browser", action="store_true", help="não abrir a interface automaticamente")
    args = parser.parse_args(argv)

    if args.modo == "cli":
        _cli()
    elif args.modo == "relatorio":
        from .report import generate_report
        print(generate_report())
    elif args.modo == "status":
        from .system_monitor import snapshot
        print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    else:
        from .config import settings
        from .server import run
        if not args.no_browser:
            url = f"http://{settings.host}:{settings.port}"
            threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
        run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
