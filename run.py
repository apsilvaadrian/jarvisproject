"""Atalho para iniciar sem configurar PYTHONPATH: python run.py [servidor|cli|relatorio|status]."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from jarvis.__main__ import main  # noqa: E402

raise SystemExit(main())
