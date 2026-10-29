"""Geração de relatórios de recursos do sistema em reports/."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import system_monitor as sm
from .config import settings


def generate_report() -> Path:
    settings.ensure_dirs()
    sm.temperature_info(wait=True)  # garante leitura atual dos sensores no relatório
    s = sm.snapshot(include_processes=False)
    procs = sm.list_processes("memoria", 10)
    now = datetime.now()
    lines = [
        "=" * 60,
        "  J.A.R.V.I.S. — RELATÓRIO DE RECURSOS DO SISTEMA",
        f"  Gerado em {now:%d/%m/%Y %H:%M:%S}",
        "=" * 60,
        "",
        "[SISTEMA]",
        *(f"  {k:<16}: {v}" for k, v in s["sistema"].items()),
        "",
        "[CPU]",
        f"  Uso total       : {s['cpu']['percent']}%",
        f"  Núcleos         : {s['cpu']['nucleos_fisicos']} físicos / {s['cpu']['nucleos_logicos']} lógicos",
        f"  Frequência      : {s['cpu']['frequencia_mhz']} MHz",
        f"  Load average    : {s['cpu']['load_average'] or 'n/d (Windows)'}",
        "",
        "[MEMÓRIA]",
        f"  RAM             : {s['memoria']['usado_gb']} / {s['memoria']['total_gb']} GB ({s['memoria']['percent']}%)",
        f"  Swap            : {s['memoria']['swap_percent']}% de {s['memoria']['swap_total_gb']} GB",
        "",
        "[DISCOS]",
        *(f"  {d['ponto_montagem']:<15} {d['usado_gb']:>8} / {d['total_gb']:<8} GB ({d['percent']}%)  {d['sistema_arquivos']}"
          for d in s["discos"]),
        "",
        "[TEMPERATURA]",
        f"  CPU             : {s['temperatura']['cpu_c'] if s['temperatura']['cpu_c'] is not None else 'indisponível'}",
        *(f"  GPU {g['nome']}: {g['temp_c']} °C, {g['percent']}% uso" for g in s["gpu"]),
        "",
        "[TOP 10 PROCESSOS POR MEMÓRIA]",
        f"  {'PID':>7}  {'MB':>9}  {'%CPU':>5}  NOME",
        *(f"  {p['pid']:>7}  {p['memoria_mb']:>9}  {p['cpu_percent']:>5}  {p['nome']}" for p in procs),
        "",
    ]
    path = settings.report_dir / f"relatorio_{now:%Y%m%d_%H%M%S}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
