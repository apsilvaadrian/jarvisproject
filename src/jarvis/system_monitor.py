"""Monitoramento de recursos do sistema: CPU, memória, disco, rede, temperatura, processos.

Usa psutil, que lê /proc e /sys no Linux e as APIs do kernel no Windows.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

import psutil

IS_WINDOWS = os.name == "nt"

_net_lock = threading.Lock()
_last_net = (time.monotonic(), psutil.net_io_counters())

# %CPU = tempo ocupado / tempo total entre duas leituras dos contadores do kernel
# (a mesma conta que o monitor.sh faz com /proc/stat). Guardamos a leitura anterior
# globalmente porque o servidor consulta a partir de threads diferentes.
_cpu_lock = threading.Lock()
_last_cpu = (psutil.cpu_times(), psutil.cpu_times(percpu=True))
_last_cpu_ts = time.monotonic()
_last_cpu_result: tuple[float, list[float]] | None = None


def _busy_percent(t1, t2) -> float:
    idle = lambda t: t.idle + getattr(t, "iowait", 0.0)
    total = lambda t: sum(t)
    dt = total(t2) - total(t1)
    if dt <= 0:
        return 0.0
    return round(max(0.0, min(100.0, 100 * (dt - (idle(t2) - idle(t1))) / dt)), 1)


def _cpu_percent() -> tuple[float, list[float]]:
    global _last_cpu, _last_cpu_ts, _last_cpu_result
    with _cpu_lock:
        # Intervalos muito curtos dão leituras ruidosas: reaproveita a última medição
        if _last_cpu_result is not None and time.monotonic() - _last_cpu_ts < 0.5:
            return _last_cpu_result
        if _last_cpu_result is None and time.monotonic() - _last_cpu_ts < 0.1:
            time.sleep(0.1)
        now = (psutil.cpu_times(), psutil.cpu_times(percpu=True))
        last, _last_cpu, _last_cpu_ts = _last_cpu, now, time.monotonic()
        _last_cpu_result = (_busy_percent(last[0], now[0]),
                            [_busy_percent(a, b) for a, b in zip(last[1], now[1])])
        return _last_cpu_result


def _gb(n: float) -> float:
    return round(n / 1024**3, 2)


def cpu_info() -> dict[str, Any]:
    freq = psutil.cpu_freq()
    try:
        load = [round(x, 2) for x in psutil.getloadavg()]
    except (AttributeError, OSError):
        load = None
    total, per_core = _cpu_percent()
    return {
        "percent": total,
        "por_nucleo": per_core,
        "nucleos_fisicos": psutil.cpu_count(logical=False),
        "nucleos_logicos": psutil.cpu_count(logical=True),
        "frequencia_mhz": round(freq.current) if freq else None,
        "load_average": load,
        "modelo": platform.processor() or platform.machine(),
    }


def memory_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return {
        "total_gb": _gb(vm.total),
        "usado_gb": _gb(vm.total - vm.available),
        "disponivel_gb": _gb(vm.available),
        "percent": vm.percent,
        "swap_total_gb": _gb(sw.total),
        "swap_percent": sw.percent,
    }


def disk_info() -> list[dict[str, Any]]:
    disks = []
    for part in psutil.disk_partitions(all=False):
        if IS_WINDOWS and ("cdrom" in part.opts or not part.fstype):
            continue
        if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay"):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "ponto_montagem": part.mountpoint,
            "dispositivo": part.device,
            "sistema_arquivos": part.fstype,
            "total_gb": _gb(usage.total),
            "usado_gb": _gb(usage.used),
            "livre_gb": _gb(usage.free),
            "percent": usage.percent,
        })
    return disks


def network_info() -> dict[str, Any]:
    """Velocidade de rede calculada pela diferença entre duas leituras."""
    global _last_net
    with _net_lock:
        now, counters = time.monotonic(), psutil.net_io_counters()
        last_t, last_c = _last_net
        dt = max(now - last_t, 1e-6)
        _last_net = (now, counters)
    return {
        "upload_kbps": round((counters.bytes_sent - last_c.bytes_sent) / dt / 1024, 1),
        "download_kbps": round((counters.bytes_recv - last_c.bytes_recv) / dt / 1024, 1),
        "enviado_total_mb": round(counters.bytes_sent / 1024**2, 1),
        "recebido_total_mb": round(counters.bytes_recv / 1024**2, 1),
    }


def battery_info() -> dict[str, Any] | None:
    try:
        b = psutil.sensors_battery()
    except (AttributeError, NotImplementedError):
        return None
    if b is None:
        return None
    return {"percent": round(b.percent), "carregando": bool(b.power_plugged)}


# --------------------------------------------------------------------------- temperatura
class _TempCache:
    """Leitura de temperatura pode levar segundos no Windows (WMI): é feita numa thread
    em segundo plano e quem consulta recebe sempre o último valor, sem esperar."""

    def __init__(self, ttl: float = 10.0):
        self.ttl = ttl
        self.value: dict[str, Any] = {"cpu_c": None, "sensores": [], "fonte": None}
        self.ts = -1e9
        self.refreshing = False
        self.lock = threading.Lock()

    def _refresh(self) -> None:
        try:
            value = _read_temperatures()
        except Exception:
            value = self.value
        with self.lock:
            self.value = value
            # Se nada foi encontrado, tenta de novo só depois de 60s
            self.ttl = 10.0 if value["cpu_c"] is not None else 60.0
            self.ts = time.monotonic()
            self.refreshing = False

    def get(self, wait: bool = False) -> dict[str, Any]:
        with self.lock:
            stale = time.monotonic() - self.ts > self.ttl and not self.refreshing
            if stale:
                self.refreshing = True
        if stale:
            if wait:
                self._refresh()
            else:
                threading.Thread(target=self._refresh, daemon=True).start()
        return self.value


def _run_powershell_json(cmd: str, timeout: float = 6.0) -> Any:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd + " | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if out.returncode != 0 or not out.stdout.strip():
        return None
    data = json.loads(out.stdout)
    return data if isinstance(data, list) else [data]


def _read_temperatures() -> dict[str, Any]:
    sensores: list[dict[str, Any]] = []
    fonte = None

    # Linux: /sys/class/hwmon e /sys/class/thermal via psutil
    if hasattr(psutil, "sensors_temperatures"):
        try:
            for chip, entries in psutil.sensors_temperatures().items():
                for e in entries:
                    if e.current and e.current > 0:
                        sensores.append({"nome": f"{chip} {e.label}".strip(), "c": round(e.current, 1)})
            fonte = "psutil"
        except Exception:
            pass

    if IS_WINDOWS and not sensores:
        # 1) LibreHardwareMonitor / OpenHardwareMonitor rodando expõem sensores via WMI
        for ns in ("root/LibreHardwareMonitor", "root/OpenHardwareMonitor"):
            try:
                data = _run_powershell_json(
                    f"Get-CimInstance -Namespace {ns} -ClassName Sensor -ErrorAction Stop | "
                    "Where-Object {{ $_.SensorType -eq 'Temperature' }} | Select-Object Name, Value"
                )
            except Exception:
                data = None
            if data:
                sensores = [{"nome": d["Name"], "c": round(float(d["Value"]), 1)} for d in data if d.get("Value")]
                fonte = ns.split("/")[1]
                break
        # 2) ACPI thermal zone (pode exigir executar como administrador)
        if not sensores:
            try:
                data = _run_powershell_json(
                    "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
                    "-ErrorAction Stop | Select-Object InstanceName, CurrentTemperature"
                )
            except Exception:
                data = None
            if data:
                sensores = [
                    {"nome": d["InstanceName"].split("\\")[-1], "c": round(d["CurrentTemperature"] / 10 - 273.15, 1)}
                    for d in data if d.get("CurrentTemperature")
                ]
                fonte = "ACPI"

    cpu_c = None
    if sensores:
        prefer = [s for s in sensores if any(k in s["nome"].lower() for k in ("cpu", "package", "core", "tctl", "k10temp"))]
        cpu_c = max(s["c"] for s in (prefer or sensores))
    return {"cpu_c": cpu_c, "sensores": sensores[:12], "fonte": fonte}


_temps = _TempCache()


def temperature_info(wait: bool = False) -> dict[str, Any]:
    return _temps.get(wait)


def gpu_info() -> list[dict[str, Any]]:
    """GPUs NVIDIA via nvidia-smi (se instalado)."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return []
    gpus = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5:
            name, util, temp, used, total = parts
            gpus.append({"nome": name, "percent": float(util), "temp_c": float(temp),
                         "mem_usada_mb": float(used), "mem_total_mb": float(total)})
    return gpus


_gpu_cache: dict[str, Any] = {"ts": 0.0, "value": []}


def _gpu_cached() -> list[dict[str, Any]]:
    if time.monotonic() - _gpu_cache["ts"] > 3:
        _gpu_cache["value"] = gpu_info()
        _gpu_cache["ts"] = time.monotonic()
    return _gpu_cache["value"]


# --------------------------------------------------------------------------- processos
def list_processes(sort_by: str = "cpu", limit: int = 10, name_filter: str = "") -> list[dict[str, Any]]:
    """Lista processos ordenados por CPU ou memória (PID, nome, estado, usuário, %CPU, MB)."""
    procs = []
    ncpu = psutil.cpu_count() or 1
    # No Linux o estado vem barato de /proc/<pid>/stat; no Windows custa segundos para ~300 processos
    attrs = ["pid", "name", "username", "memory_info", "cpu_percent"] + ([] if IS_WINDOWS else ["status"])
    for p in psutil.process_iter(attrs):
        info = p.info
        name = info.get("name") or ""
        if name_filter and name_filter.lower() not in name.lower():
            continue
        if info["pid"] == 0:  # "System Idle Process" no Windows
            continue
        mem = info.get("memory_info")
        procs.append({
            "pid": info["pid"],
            "nome": name,
            "estado": info.get("status", "running"),
            "usuario": (info.get("username") or "").split("\\")[-1],
            "cpu_percent": round((info.get("cpu_percent") or 0.0) / ncpu, 1),
            "memoria_mb": round(mem.rss / 1024**2, 1) if mem else 0.0,
        })
    key = "memoria_mb" if sort_by.startswith("mem") else "cpu_percent"
    procs.sort(key=lambda x: x[key], reverse=True)
    return procs[: max(1, int(limit))]


# "Aquece" o cache de processos do psutil para que %CPU já venha medido na 1ª consulta real.
list_processes(limit=1)


def system_info() -> dict[str, Any]:
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime = datetime.now() - boot
    h, rem = divmod(int(uptime.total_seconds()), 3600)
    return {
        "sistema": f"{platform.system()} {platform.release()}",
        "versao": platform.version(),
        "arquitetura": platform.machine(),
        "hostname": socket.gethostname(),
        "usuario": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "boot": boot.isoformat(timespec="seconds"),
        "uptime": f"{h}h {rem // 60}min",
        "python": platform.python_version(),
        "total_processos": len(psutil.pids()),
    }


def snapshot(include_processes: bool = True) -> dict[str, Any]:
    """Foto completa do sistema, enviada periodicamente à interface."""
    data = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "cpu": cpu_info(),
        "memoria": memory_info(),
        "discos": disk_info(),
        "rede": network_info(),
        "temperatura": temperature_info(),
        "gpu": _gpu_cached(),
        "bateria": battery_info(),
        "sistema": system_info(),
    }
    if include_processes:
        data["processos"] = _top_processes_cached()
    return data


_proc_cache: dict[str, Any] = {"ts": 0.0, "value": []}


def _top_processes_cached() -> list[dict[str, Any]]:
    if time.monotonic() - _proc_cache["ts"] > 3:
        _proc_cache["value"] = list_processes("cpu", 6)
        _proc_cache["ts"] = time.monotonic()
    return _proc_cache["value"]


def summary_text() -> str:
    """Resumo curto em linguagem natural (usado pelo cérebro offline e relatórios)."""
    s = snapshot(include_processes=False)
    parts = [
        f"CPU em {s['cpu']['percent']:.0f}%",
        f"memória em {s['memoria']['percent']:.0f}% ({s['memoria']['usado_gb']} de {s['memoria']['total_gb']} GB)",
    ]
    if s["discos"]:
        d = s["discos"][0]
        parts.append(f"disco {d['ponto_montagem']} com {d['livre_gb']} GB livres ({d['percent']:.0f}% ocupado)")
    t = s["temperatura"]["cpu_c"]
    if t is not None:
        parts.append(f"temperatura da CPU em {t:.0f} °C")
    for g in s["gpu"]:
        parts.append(f"GPU {g['nome']} a {g['temp_c']:.0f} °C com {g['percent']:.0f}% de uso")
    return ", ".join(parts) + "."
