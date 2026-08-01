"""
Piezas compartidas entre scrape_gpus.py (PCComponentes, gráficas nuevas) y
scrape_gpus_cex.py (CeX, gráficas de segunda mano/reacondicionadas): parseo
de chip/VRAM a partir del nombre del producto y la tabla de rendimiento
relativo, para que ambas tiendas puntúen con el mismo criterio y sean
comparables entre sí (ver compare_gpus.py).
"""
import re

KNOWN_BRANDS_RE = re.compile(
    r"ASUS|MSI|Gigabyte|Sapphire|PowerColor|PNY|Zotac|Palit|Gainward|XFX|Inno3D|"
    r"EVGA|ASRock|Manli",
    re.I,
)

# Modelo exacto de chip a partir del nombre del producto. Se buscan primero
# los patrones más específicos (SUPER/Ti/XT/XTX/GRE) para no confundir p.ej.
# "RTX 4070" con "RTX 4070 Ti". Cubre desde Maxwell/Polaris (lo que vende
# CeX de segunda mano) hasta la generación actual (lo que vende PCComponentes
# nuevo), para que el mismo regex sirva en las dos tiendas.
CHIP_RE = re.compile(
    r"(RTX\s?50\d0\s?Ti|RTX\s?50\d0|"
    r"RTX\s?40\d0\s?Ti\s?SUPER|RTX\s?40\d0\s?SUPER|RTX\s?40\d0\s?Ti|RTX\s?40\d0|"
    r"RTX\s?30\d0\s?Ti|RTX\s?30\d0|"
    r"RTX\s?20\d0\s?Ti|RTX\s?20\d0\s?SUPER|RTX\s?20\d0|"
    r"GTX\s?16\d0\s?Ti|GTX\s?16\d0\s?SUPER|GTX\s?16\d0|"
    r"GTX\s?10\d0\s?Ti|GTX\s?10\d0|"
    r"GTX\s?9\d0\s?Ti|GTX\s?9\d0|"
    r"RX\s?9\d{3}\s?XT|RX\s?9\d{3}|"
    r"RX\s?7\d{3}\s?XTX|RX\s?7\d{3}\s?XT|RX\s?7\d{3}|"
    r"RX\s?6\d{3}\s?XT|RX\s?6\d{3}|"
    r"RX\s?5\d{3}\s?XT|RX\s?5\d{3}|"
    r"RX\s?5[6-9]0)",
    re.I,
)


def normalize_chip(raw):
    """Normaliza espacios/mayúsculas para que coincida con las claves de
    GPU_RELATIVE_PERFORMANCE (ej. 'rtx 5060  ti' -> 'RTX 5060 Ti', y
    'RTX 4070Ti' sin espacio -> 'RTX 4070 Ti', que es como viene el nombre
    en bastantes fichas de producto)."""
    text = re.sub(r"\s+", " ", raw.strip()).upper()
    # separa el número del sufijo cuando vienen pegados (4070TI -> 4070 TI)
    text = re.sub(r"(\d)(TI|SUPER|XTX|XT|GRE)\b", r"\1 \2", text)
    fix = {"TI": "Ti", "SUPER": "SUPER", "XT": "XT", "XTX": "XTX", "GRE": "GRE"}
    parts = [fix.get(p, p) for p in text.split(" ")]
    return " ".join(parts)


# Índice de rendimiento relativo (aprox., no oficial) basado en benchmarks
# agregados públicos (TechPowerUp / Notebookcheck, consultado jul-2026),
# normalizado con RTX 4090 = 100. Sirve para no rankear por "más núcleos" o
# "más GB" en bruto. Puede variar ±10-15% según el modelo concreto (versión
# OC vs. versión base) y no se actualiza sola. Los chips previos a Maxwell/
# Polaris (GT 7xx, HD 7xxx, GTX 6xx...) quedan fuera a propósito: no hay
# benchmark fiable agregado y CeX los marca como SIN_DATO_BENCHMARK en vez
# de inventar un número.
GPU_RELATIVE_PERFORMANCE = {
    "RTX 5090": 145, "RTX 5080": 108, "RTX 5070 Ti": 95, "RTX 5070": 78,
    "RTX 5060 Ti": 61, "RTX 5060": 50, "RTX 5050": 38,
    "RTX 4090": 100, "RTX 4080 Ti SUPER": 92, "RTX 4080 SUPER": 92, "RTX 4080": 88,
    "RTX 4070 Ti SUPER": 82, "RTX 4070 Ti": 78, "RTX 4070 SUPER": 74, "RTX 4070": 68,
    "RTX 4060 Ti": 55, "RTX 4060": 47,
    "RTX 3090 Ti": 78, "RTX 3090": 74, "RTX 3080 Ti": 72, "RTX 3080": 68,
    "RTX 3070 Ti": 60, "RTX 3070": 57, "RTX 3060 Ti": 50, "RTX 3060": 42,
    "RTX 3050": 27,
    "RTX 2080 Ti": 48, "RTX 2080 SUPER": 43, "RTX 2080": 40,
    "RTX 2070 SUPER": 38, "RTX 2070": 34,
    "RTX 2060 SUPER": 32, "RTX 2060": 28,
    "GTX 1660 Ti": 30, "GTX 1660 SUPER": 29, "GTX 1660": 26,
    "GTX 1650 SUPER": 20, "GTX 1650": 16,
    "GTX 1080 Ti": 38, "GTX 1080": 32,
    "GTX 1070 Ti": 28, "GTX 1070": 25,
    "GTX 1060": 17, "GTX 1050 Ti": 10, "GTX 1050": 8,
    "GTX 980 Ti": 26, "GTX 980": 20, "GTX 970": 17,
    "GTX 960": 9, "GTX 950": 7,
    "RX 9070 XT": 90, "RX 9070": 80, "RX 9060 XT": 58, "RX 9060": 48,
    "RX 7900 XTX": 98, "RX 7900 XT": 88, "RX 7900 GRE": 78,
    "RX 7800 XT": 70, "RX 7700 XT": 62, "RX 7600 XT": 48, "RX 7600": 44,
    "RX 6950 XT": 75, "RX 6900 XT": 70, "RX 6800 XT": 65, "RX 6800": 60,
    "RX 6750 XT": 55, "RX 6700 XT": 52, "RX 6650 XT": 42, "RX 6600 XT": 40,
    "RX 6600": 36, "RX 6500 XT": 22, "RX 6400": 15,
    "RX 5700 XT": 35, "RX 5700": 32, "RX 5600 XT": 28, "RX 5500 XT": 18,
    "RX 590": 19, "RX 580": 17, "RX 570": 14, "RX 560": 8,
}


def extract_specs(name):
    chip_m = CHIP_RE.search(name)
    chip = normalize_chip(chip_m.group(1)) if chip_m else None

    vram_m = re.search(r"(\d{1,2})\s*GB\s*(GDDR\d\w*|DDR\d|HBM\d)?", name, re.I)
    vram_gb = int(vram_m.group(1)) if vram_m else None

    return {
        "chip": chip,
        "vram_gb": vram_gb,
        "relative_perf": GPU_RELATIVE_PERFORMANCE.get(chip) if chip else None,
    }


def is_known_brand(name):
    return bool(KNOWN_BRANDS_RE.search(name))
