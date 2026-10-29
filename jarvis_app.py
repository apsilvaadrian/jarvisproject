"""Ponto de entrada do JARVIS.exe (PyInstaller). Para desenvolver, use run.py ou python -m jarvis."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from jarvis.app import main  # noqa: E402

raise SystemExit(main())
