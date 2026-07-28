"""
Scraper de tarjetas gráficas en pccomponentes.com, pensado para encontrar la
mejor relación rendimiento/precio para juego/uso general (no ficha técnica
bruta: una GPU con más núcleos no gana si su rendimiento real por euro es peor).

Uso:
    python scrape_gpus.py [TOP_N]

    TOP_N (opcional, por defecto 15): a cuántas de las mejores se les visita
    la ficha de producto para sacar TDP, núcleos, bus/ancho de banda de
    memoria y velocidades (datos que no están en el listado).

Genera:
    gpus.csv / gpus.json -> todas las gráficas encontradas con specs
    Imprime el top TOP_N con ficha detallada y "value_score".
"""
import json
import re
import sys
import time
import csv
import urllib.request

BASE_URL = "https://www.pccomponentes.com/categorias/tarjetas-graficas"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
}

KNOWN_BRANDS_RE = re.compile(
    r"ASUS|MSI|Gigabyte|Sapphire|PowerColor|PNY|Zotac|Palit|Gainward|XFX|Inno3D",
    re.I,
)

# Modelo exacto de chip a partir del nombre del producto. Se buscan primero
# los patrones más específicos (SUPER/Ti/XT/XTX/GRE) para no confundir p.ej.
# "RTX 4070" con "RTX 4070 Ti".
CHIP_RE = re.compile(
    r"(RTX\s?50\d0\s?Ti|RTX\s?50\d0|"
    r"RTX\s?40\d0\s?Ti\s?SUPER|RTX\s?40\d0\s?SUPER|RTX\s?40\d0\s?Ti|RTX\s?40\d0|"
    r"RTX\s?30\d0\s?Ti|RTX\s?30\d0|"
    r"GTX\s?16\d0\s?Ti|GTX\s?16\d0\s?SUPER|GTX\s?16\d0|"
    r"RX\s?9\d{3}\s?XT|RX\s?9\d{3}|"
    r"RX\s?7\d{3}\s?XTX|RX\s?7\d{3}\s?XT|RX\s?7\d{3}|"
    r"RX\s?6\d{3}\s?XT|RX\s?6\d{3})",
    re.I,
)


def normalize_chip(raw):
    """Normaliza espacios/mayúsculas para que coincida con las claves de
    GPU_RELATIVE_PERFORMANCE (ej. 'rtx 5060  ti' -> 'RTX 5060 Ti')."""
    parts = re.sub(r"\s+", " ", raw.strip()).upper().split(" ")
    # Ti/SUPER/XT/XTX/GRE con capitalización propia para que se lea bien en salida
    fix = {"TI": "Ti", "SUPER": "SUPER", "XT": "XT", "XTX": "XTX", "GRE": "GRE"}
    parts = [fix.get(p, p) for p in parts]
    return " ".join(parts)


# Índice de rendimiento relativo (aprox., no oficial) basado en benchmarks
# agregados públicos (TechPowerUp / Notebookcheck, consultado jul-2026),
# normalizado con RTX 4090 = 100. Sirve para no rankear por "más núcleos" o
# "más GB" en bruto: dos tarjetas con el mismo chip pero distinto rendimiento
# real por su TDP/clocks de fábrica quedan en la misma franja aquí, que es la
# resolución que importa para decidir la compra. Puede variar ±10-15% según
# el modelo concreto (versión OC vs. versión base) y no se actualiza sola.
GPU_RELATIVE_PERFORMANCE = {
    "RTX 5090": 145, "RTX 5080": 108, "RTX 5070 Ti": 95, "RTX 5070": 78,
    "RTX 5060 Ti": 61, "RTX 5060": 50,
    "RTX 4090": 100, "RTX 4080 Ti SUPER": 92, "RTX 4080 SUPER": 92, "RTX 4080": 88,
    "RTX 4070 Ti SUPER": 82, "RTX 4070 Ti": 78, "RTX 4070 SUPER": 74, "RTX 4070": 68,
    "RTX 4060 Ti": 55, "RTX 4060": 47,
    "RTX 3090 Ti": 78, "RTX 3090": 74, "RTX 3080 Ti": 72, "RTX 3080": 68,
    "RTX 3070 Ti": 60, "RTX 3070": 57, "RTX 3060 Ti": 50, "RTX 3060": 42,
    "GTX 1660 Ti": 30, "GTX 1660 SUPER": 29, "GTX 1660": 26,
    "RX 9070 XT": 90, "RX 9070": 80,
    "RX 7900 XTX": 98, "RX 7900 XT": 88, "RX 7900 GRE": 78,
    "RX 7800 XT": 70, "RX 7700 XT": 62, "RX 7600 XT": 48, "RX 7600": 44,
    "RX 6950 XT": 75, "RX 6900 XT": 70, "RX 6800 XT": 65, "RX 6800": 60,
    "RX 6750 XT": 55, "RX 6700 XT": 52, "RX 6650 XT": 42, "RX 6600 XT": 40,
    "RX 6600": 36, "RX 6500 XT": 22, "RX 6400": 15,
}


def fetch(url, retries=4):
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt * 3)
    raise last_err


def parse_products(html):
    idx = html.find('microdata-product-list-script')
    if idx == -1:
        return [], 1
    start = html.find(">", idx) + 1
    end = html.find("</script>", start)
    data = json.loads(html[start:end])
    total_pages_match = re.search(r'"totalPages\\?":(\d+)', html)
    total_pages = int(total_pages_match.group(1)) if total_pages_match else 1
    products = []
    for entry in data.get("itemListElement", []):
        item = entry["item"]
        products.append({
            "name": item["name"],
            "url": item["url"],
            "sku": item.get("sku", ""),
            "price": item["offers"]["price"],
            "rating": item.get("aggregateRating", {}).get("ratingValue"),
            "rating_count": item.get("aggregateRating", {}).get("ratingCount"),
        })
    return products, total_pages


def extract_specs(name):
    chip_m = CHIP_RE.search(name)
    chip = normalize_chip(chip_m.group(1)) if chip_m else None

    vram_m = re.search(r"(\d{1,2})\s*GB\s*(GDDR\d\w*|DDR\d)?", name, re.I)
    vram_gb = int(vram_m.group(1)) if vram_m else None

    return {
        "chip": chip,
        "vram_gb": vram_gb,
        "relative_perf": GPU_RELATIVE_PERFORMANCE.get(chip) if chip else None,
    }


def is_known_brand(name):
    return bool(KNOWN_BRANDS_RE.search(name))


def rating_count_of(p):
    try:
        return int(p.get("rating_count") or 0)
    except (TypeError, ValueError):
        return 0


def score(p):
    """Puntuación de valor real (rendimiento por euro), no ficha técnica bruta.
    Sin dato de benchmark para el chip, se puntúa muy bajo y se marca con
    alerta en vez de asumir un rendimiento inventado."""
    s = p["specs"]
    price = float(p["price"])

    if not s["relative_perf"]:
        return 1.0

    # Núcleo del score: rendimiento relativo por cada 100€ de precio.
    perf_per_100eur = s["relative_perf"] / (price / 100)
    val = perf_per_100eur * 10

    # VRAM: penaliza 8GB en gama media/alta (cuello de botella ya hoy en
    # varios juegos a 1440p+), premia 12-16GB.
    vram = s["vram_gb"] or 0
    if vram >= 16:
        val += 10
    elif vram >= 12:
        val += 7
    elif vram == 8:
        val += 2
    else:
        val += 0

    # Marca con trayectoria conocida en tarjetas gráficas (garantía, soporte,
    # calidad de VRM/refrigeración más consistente).
    if is_known_brand(p["name"]):
        val += 8
    else:
        val += 1

    # Rating de otros compradores
    try:
        rating = float(p.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    rc = rating_count_of(p)
    val += rating * 2
    val += min(rc / 30, 4)

    return round(val, 2)


def alert_of(p):
    s = p["specs"]
    if not s["relative_perf"]:
        return "SIN_DATO_BENCHMARK"
    if (s["vram_gb"] or 0) <= 8 and s["relative_perf"] >= 70:
        return "INVESTIGAR"  # gráfica potente con poca VRAM: puede quedarse corta pronto
    if is_known_brand(p["name"]) and rating_count_of(p) >= 15:
        return "COMPRA_SEGURA"
    return ""


# La ficha de producto usa etiquetas <strong>Campo</strong> distintas según
# fabricante/plantilla (ej. Palit dice "Consumo" y "Ancho de bus" donde otros
# dicen "TDP" y "Bus de memoria"), así que cada campo prueba varias etiquetas
# conocidas por orden hasta encontrar una.
DETAIL_FIELD_LABELS = {
    "gpu_chip_detail": [r"Procesador gr[^<]*"],
    "memory_detail": [r"Memoria VRAM", r"Memoria"],
    "tdp_detail": [r"TDP", r"Consumo"],
    "cuda_cores_detail": [r"N[^<]*cleos[^<]*"],
    "bandwidth_detail": [r"Ancho de banda"],
    "memory_bus_detail": [r"Bus de memoria", r"Ancho de bus"],
    "boost_clock_detail": [r"Velocidad boost", r"Frecuencia base\s*/\s*boost", r"Velocidad memoria"],
    "power_connector_detail": [r"Conector alimentaci[^<]*"],
}
DETAIL_FIELD_PATTERNS = {
    field: [
        pattern
        for label in labels
        for pattern in (
            re.compile(r"<strong>" + label + r"</strong></td>\s*<td>([^<]*)</td>", re.I),
            re.compile(r"<strong>" + label + r"</strong>\s*([^<]*)</li>", re.I),
        )
    ]
    for field, labels in DETAIL_FIELD_LABELS.items()
}


def fetch_product_detail(url):
    clean_url = url.split("?")[0]
    try:
        html = fetch(clean_url, retries=3)
    except Exception as e:
        return {"error": str(e)}

    result = {}
    for field, patterns in DETAIL_FIELD_PATTERNS.items():
        raw = None
        for pattern in patterns:
            m = pattern.search(html)
            if m:
                raw = re.sub(r"&quot;|&#34;", '"', m.group(1)).strip()
                break
        result[field] = raw

    tdp_m = re.search(r"(\d{2,4})\s*W", result.get("tdp_detail") or "", re.I)
    result["tdp_w"] = int(tdp_m.group(1)) if tdp_m else None

    return result


def main():
    all_products = []
    print(f"Descargando página 1: {BASE_URL}", file=sys.stderr)
    html = fetch(BASE_URL)
    products, total_pages = parse_products(html)
    all_products.extend(products)
    print(f"Total de páginas detectadas: {total_pages}", file=sys.stderr)

    for page in range(2, total_pages + 1):
        url = f"{BASE_URL}?page={page}"
        print(f"Descargando página {page}/{total_pages}...", file=sys.stderr)
        time.sleep(1.5)
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  error en página {page}: {e}", file=sys.stderr)
            continue
        products, _ = parse_products(html)
        all_products.extend(products)

    seen = set()
    deduped = []
    for p in all_products:
        if p["sku"] in seen:
            continue
        seen.add(p["sku"])
        deduped.append(p)

    for p in deduped:
        p["specs"] = extract_specs(p["name"])
        p["value_score"] = score(p)
        p["alerta"] = alert_of(p)

    deduped.sort(key=lambda p: p["value_score"], reverse=True)

    with open("gpus.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["value_score", "alerta", "name", "price", "chip", "vram_gb",
                          "relative_perf", "rating", "rating_count", "url"])
        for p in deduped:
            s = p["specs"]
            writer.writerow([p["value_score"], p["alerta"], p["name"], p["price"],
                              s["chip"], s["vram_gb"], s["relative_perf"], p["rating"],
                              p["rating_count"], p["url"]])

    with open("gpus.json", "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    top = deduped[:top_n]

    print(f"\nTotal gráficas únicas encontradas: {len(deduped)}\n")
    print(f"Consultando ficha de producto de las {top_n} mejores (TDP, núcleos, "
          f"bus/ancho de banda de memoria)...", file=sys.stderr)
    for i, p in enumerate(top, 1):
        print(f"  {i}/{top_n}: {p['name'][:60]}", file=sys.stderr)
        detail = fetch_product_detail(p["url"])
        for field in DETAIL_FIELD_PATTERNS:
            p["specs"][field] = detail.get(field)
        p["specs"]["tdp_w"] = detail.get("tdp_w")
        time.sleep(1.2)

    with open("gpus_top_enriched.json", "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)

    print(f"\n=== TOP {top_n} tarjetas gráficas por rendimiento/precio ===\n")
    for i, p in enumerate(top, 1):
        s = p["specs"]
        print(f"{i}. [{p['value_score']}] {p['alerta'] or '-'} {p['name']} - {p['price']}€")
        print(f"   Chip: {s.get('gpu_chip_detail') or s['chip'] or 'sin dato'} | "
              f"Rendimiento relativo (RTX4090=100): {s['relative_perf'] or 'sin dato'}")
        print(f"   VRAM: {s.get('memory_detail') or s['vram_gb']} | "
              f"TDP: {s.get('tdp_detail') or 'sin dato'} | "
              f"Núcleos: {s.get('cuda_cores_detail') or 'sin dato'}")
        print(f"   Bus memoria: {s.get('memory_bus_detail') or 'sin dato'} | "
              f"Ancho de banda: {s.get('bandwidth_detail') or 'sin dato'} | "
              f"Boost: {s.get('boost_clock_detail') or 'sin dato'}")
        print(f"   Conector alimentación: {s.get('power_connector_detail') or 'sin dato'}")
        print(f"   Rating: {p['rating']} ({p['rating_count']})")
        print(f"   {p['url']}\n")


if __name__ == "__main__":
    main()
