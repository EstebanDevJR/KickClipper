"""Memoria del equipo y modelo de Ollama recomendado para él.

Sin dependencias extra: RAM con la API del sistema; VRAM con nvidia-smi
(NVIDIA) o, para AMD, el registro de Windows / sysfs en Linux (las dos marcas
que Ollama acelera). En Apple Silicon la memoria es unificada: la GPU usa la RAM.
"""
from __future__ import annotations

import ctypes
import functools
import os
import platform
import shutil
import subprocess
from pathlib import Path

# (memoria mínima en GB, modelo) de mayor a menor; los tamaños son los de
# Ollama con cuantización Q4 (4b ≈ 2,5 GB, 8b ≈ 5,2 GB, 14b ≈ 9,3 GB).
_GPU_TIERS = [(12, "qwen3:14b"), (6, "qwen3:8b"), (0, "qwen3:4b")]
_UNIFIED_TIERS = [(24, "qwen3:14b"), (12, "qwen3:8b"), (0, "qwen3:4b")]


def ram_gb() -> float | None:
    try:
        if platform.system() == "Windows":
            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / 2**30
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except Exception:
        return None


def vram_gb() -> tuple[float, str] | None:
    """(VRAM en GB, marca) de la GPU NVIDIA o AMD más grande, o None si no hay."""
    found = [g for g in (_nvidia(), _amd()) if g]
    return max(found) if found else None


def _nvidia() -> tuple[float, str] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        values = [float(v) for v in out.split() if v.strip().replace(".", "", 1).isdigit()]
        return (max(values) / 1024, "NVIDIA") if values else None
    except Exception:
        return None


def _amd() -> tuple[float, str] | None:
    sizes: list[float] = []
    try:
        if platform.system() == "Windows":
            # WMI (AdapterRAM) se queda en 4 GB; el driver guarda el tamaño real aquí
            import winreg
            key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, i)) as sub:
                            desc = str(winreg.QueryValueEx(sub, "DriverDesc")[0])
                            if "AMD" in desc or "Radeon" in desc:
                                sizes.append(int(winreg.QueryValueEx(sub, "HardwareInformation.qwMemorySize")[0]))
                    except OSError:
                        continue
        elif platform.system() == "Linux":
            for f in Path("/sys/class/drm").glob("card*/device/mem_info_vram_total"):
                if (f.parent / "vendor").read_text().strip() == "0x1002":  # AMD
                    sizes.append(int(f.read_text()))
    except Exception:
        return None
    sizes = [b for b in sizes if b >= 2**30]  # fuera gráficas integradas
    return (max(sizes) / 2**30, "AMD") if sizes else None


def recommend(ram: float | None, vram: tuple[float, str] | None, apple_silicon: bool) -> tuple[str, str]:
    """(modelo, motivo) que cabe con holgura en la memoria del equipo."""
    if apple_silicon and ram:
        model = next(m for gb, m in _UNIFIED_TIERS if ram >= gb)
        return model, f"Apple Silicon con {ram:.0f} GB de memoria unificada"
    if vram:
        gb, brand = vram
        model = next(m for tier, m in _GPU_TIERS if gb >= tier)
        return model, f"GPU {brand} con {gb:.0f} GB de VRAM"
    # solo CPU: un modelo grande tardaría demasiado en puntuar las candidatas
    return "qwen3:4b", "sin GPU compatible: el modelo pequeño responde mucho más rápido en CPU"


@functools.cache
def summary() -> dict:
    ram, vram = ram_gb(), vram_gb()
    apple = platform.system() == "Darwin" and platform.machine() == "arm64"
    model, reason = recommend(ram, vram, apple)
    return {
        "ram_gb": round(ram, 1) if ram else None,
        "vram_gb": round(vram[0], 1) if vram else None,
        "gpu": vram[1] if vram else None,
        "recommended_model": model,
        "recommended_reason": reason,
    }
