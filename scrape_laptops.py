"""
Scraper de portátiles en pccomponentes.com filtrados por precio, pensado para
evaluar cuál es mejor para un stack de desarrollo Flutter + Claude Code + GoLand
(Android Studio/emulador + GoLand + terminal, uso intensivo de RAM y CPU).

Uso:
    python scrape_laptops.py [TOP_N]

    TOP_N (opcional, por defecto 15): cuántos de los mejores portátiles
    reciben además una visita a su ficha de producto para sacar RAM ampliable
    o soldada, pantalla, batería y peso (detalle que no está en el listado).

Genera:
    laptops.csv   -> todos los portátiles encontrados con specs extraídas
    laptops.json  -> mismos datos en JSON
    Imprime el top TOP_N con ficha detallada (RAM, pantalla, batería, riesgo).
"""
import json
import re
import time
import csv
import sys
import urllib.request

BASE_URL = "https://www.pccomponentes.com/portatiles?price_from=451&price_to=783"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
}

CPU_TIERS = [
    (re.compile(r"Ryzen\s*9|Core\s*i9|Apple\s*M3\s*Pro|Apple\s*M3\s*Max|Apple\s*M4", re.I), 95),
    (re.compile(r"Ryzen\s*7|Core\s*i7|Apple\s*M3|Apple\s*M2\s*Pro", re.I), 85),
    (re.compile(r"Ryzen\s*5|Core\s*i5|Apple\s*M2|Apple\s*M1", re.I), 70),
    (re.compile(r"Ryzen\s*3|Core\s*i3", re.I), 40),
    (re.compile(r"Celeron|Pentium|N\d{3,4}|MediaTek", re.I), 15),
]

# Clase de potencia del chip (sufijo del modelo): esto predice el rendimiento
# sostenido en compilación mucho mejor que "i7 vs i5" a secas. Un i7-13620H
# (serie H, ~45W) compila más rápido y sostenido que un i7-1355U (serie U, ~15W)
# aunque ambos se anuncien como "i7".
CPU_MODEL_RE = re.compile(r"\b\d{4,5}([A-Z]{1,3})\b")
CPU_POWER_CLASS_BONUS = {
    "HX": 20, "HK": 20,
    "H": 15,
    "HS": 12,
    "P": 8,
    "U": 0,
}

# Cinebench R23 multi-core, promedios reales de Notebookcheck (agregados de
# decenas de portátiles con ese chip). Varía ±15% según refrigeración del
# equipo concreto, pero da una señal de rendimiento sostenido mucho más fiable
# que "i7 vs i5". Fuente: notebookcheck.net (consultado jul-2026).
CINEBENCH_R23_MULTI = {
    "13900HK": 16138,
    "13700H": 15098,
    "13620H": 14100,
    "12700H": 13500,   # estimado: el único dato público es un leak de preserie (18501, poco representativo)
    "7735HS": 12783,
    "11800H": 11662,
    "5825U": 11100,
    "13420H": 10679,
    "1355U": 8637,
    "9750H": 7800,     # estimado a partir de una muestra individual (HWBOT), no promedio agregado
}
# Factor para llevar el benchmark real a la misma escala que el resto del score (0-~55)
CINEBENCH_SCALE = 300

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
    ram_m = re.search(r"(\d{1,3})\s*GB(?!\w*SSD)(?!\w*HDD)", name, re.I)
    # RAM suele aparecer como "16GB" seguido de "/xxxGB SSD"; separar RAM de almacenamiento
    ram = None
    storage = None
    storage_m = re.search(r"(\d+)\s*(GB|TB)\s*SSD", name, re.I)
    if storage_m:
        val = int(storage_m.group(1))
        storage = val * 1024 if storage_m.group(2).upper() == "TB" else val
    # RAM: número justo antes de "GB" que no vaya seguido de SSD/HDD
    ram_candidates = re.findall(r"(\d{1,3})\s*GB", name, re.I)
    if storage_m and ram_candidates:
        storage_val_str = storage_m.group(1)
        ram_candidates_wo_storage = [c for c in ram_candidates if not (c == storage_val_str)]
        ram = int(ram_candidates_wo_storage[0]) if ram_candidates_wo_storage else (
            int(ram_candidates[0]) if len(ram_candidates) == 1 else None
        )
    elif ram_candidates:
        ram = int(ram_candidates[0])

    screen_m = re.search(r'(\d{2}(?:\.\d)?)\s*"', name)
    screen = float(screen_m.group(1)) if screen_m else None

    cpu = None
    cpu_tier = 0
    for pattern, tier in CPU_TIERS:
        if pattern.search(name):
            cpu_tier = tier
            m = pattern.search(name)
            cpu = m.group(0)
            break

    gpu_dedicated = bool(re.search(r"RTX|GTX|Radeon\s*RX|MX\d{3}", name, re.I))
    oled = bool(re.search(r"OLED", name, re.I))

    is_amd = bool(re.search(r"Ryzen", name, re.I))
    model_m = CPU_MODEL_RE.search(name)
    cpu_model = None
    cpu_power_class = None
    cpu_gen = None  # generación Intel, o serie AMD (7=Zen4/7000, 5=Zen3/5000, 3=Zen2/3000...)
    if model_m:
        cpu_model = model_m.group(0).upper()
        cpu_power_class = model_m.group(1).upper()
        digits = re.match(r"(\d{4,5})", cpu_model).group(1)
        # Intel: los 2 primeros dígitos de un nº de 5 cifras son la generación
        # (13620H -> gen 13); con 4 cifras (gen 7-9) no llevan ese prefijo doble
        # (9750H -> gen 9). AMD Ryzen: el primer dígito es la serie
        # (7735HS -> serie 7000 / Zen4, 5825U -> serie 5000 / Zen3).
        cpu_gen = int(digits[:2]) if len(digits) == 5 else int(digits[0])
    cpu_power_bonus = CPU_POWER_CLASS_BONUS.get(cpu_power_class, 0)
    cinebench_r23_multi = CINEBENCH_R23_MULTI.get(cpu_model)

    return {
        "ram_gb": ram,
        "storage_gb": storage,
        "screen_in": screen,
        "cpu": cpu,
        "cpu_model": cpu_model,
        "cpu_tier": cpu_tier,
        "cpu_power_class": cpu_power_class,
        "cpu_power_bonus": cpu_power_bonus,
        "cpu_gen": cpu_gen,
        "is_amd": is_amd,
        "cinebench_r23_multi": cinebench_r23_multi,
        "gpu_dedicated": gpu_dedicated,
        "oled": oled,
    }


def score(product):
    s = product["specs"]
    score_val = 0.0

    # RAM: importante para correr GoLand + Android Studio/emulador + Claude Code a la vez,
    # pero no debe eclipsar el rendimiento real de CPU en compilación.
    ram = s["ram_gb"] or 0
    if ram >= 32:
        score_val += 20
    elif ram >= 16:
        score_val += 16
    elif ram >= 12:
        score_val += 8
    else:
        score_val += 2

    # CPU: si tengo benchmark real (Cinebench R23 multi), lo uso directamente.
    # Si no, caigo en la heurística marca/gama + clase de potencia (U/P/H/HS/HX).
    if s["cinebench_r23_multi"]:
        score_val += s["cinebench_r23_multi"] / CINEBENCH_SCALE
    else:
        score_val += s["cpu_tier"] * 0.35
        score_val += s["cpu_power_bonus"]

    # Pequeño bonus/penalización por antigüedad de la plataforma (batería y
    # eficiencia se degradan con los años; series muy viejas ya acusan uso).
    gen = s["cpu_gen"] or 0
    if s["is_amd"]:
        if gen >= 7:      # Ryzen 7000+ / Zen4 (2022+)
            score_val += 4
        elif gen == 5:    # Ryzen 5000 / Zen3 (2020-21), sigue siendo sólido
            score_val += 1
        elif gen and gen <= 4:  # Ryzen 3000 o anterior / Zen2 y previos
            score_val -= 4
    else:
        if gen >= 13:     # Raptor Lake (2023+)
            score_val += 4
        elif gen >= 11:   # Tiger/Alder Lake (2021-22)
            score_val += 2
        elif gen and gen <= 10:  # 10ª gen o anterior (2019 y antes)
            score_val -= 4

    # Almacenamiento (SSD siempre mejor, valorar tamaño para SDKs/emuladores/proyectos)
    storage = s["storage_gb"] or 0
    if storage >= 1024:
        score_val += 8
    elif storage >= 512:
        score_val += 6
    elif storage >= 256:
        score_val += 3
    else:
        score_val += 0.5

    # GPU dedicada ayuda al renderizado del emulador Android / hardware accel
    if s["gpu_dedicated"]:
        score_val += 5

    # Pantalla grande cómoda para tener 2-3 IDEs/paneles a la vez
    screen = s["screen_in"] or 0
    if screen >= 15.6:
        score_val += 5
    elif screen >= 14:
        score_val += 3

    # Rating de otros compradores como desempate suave
    try:
        rating = float(product.get("rating") or 0)
        score_val += rating * 1.5
    except (TypeError, ValueError):
        pass

    # Penalizar RAM insuficiente sin posibilidad de ampliar (heurística: si <16GB, penaliza más)
    if ram and ram < 16:
        score_val -= 10

    return round(score_val, 2)


# Campos de la tabla de specs de la ficha de producto que no están en el
# listado y solo se pueden sacar visitando cada portátil individualmente.
DETAIL_FIELD_PATTERNS = {
    "ram_detail": [
        re.compile(r"<strong>Memoria RAM</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Memoria RAM</strong>\s*([^<]*)</li>", re.I),
    ],
    "screen_detail": [
        re.compile(r"<strong>Pantalla[^<]*</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Pantalla[^<]*</strong>\s*([^<]*)</li>", re.I),
    ],
    "battery_detail": [
        re.compile(r"<strong>Bater[^<]*</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Bater[^<]*</strong>\s*([^<]*)</li>", re.I),
    ],
    "weight_detail": [
        re.compile(r"<strong>Peso</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Peso</strong>\s*([^<]*)</li>", re.I),
    ],
}


def fetch_product_detail(url):
    """Visita la ficha de producto y extrae RAM (texto crudo + si es ampliable),
    pantalla, batería y peso: son datos que solo están en la ficha individual,
    no en el listado, y que importan para decidir entre finalistas."""
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

    raw_ram = result.get("ram_detail") or ""
    lower = raw_ram.lower()
    if "no ampliable" in lower or "soldada" in lower or "soldado" in lower or "integrada" in lower:
        result["ram_upgradeable"] = False
    elif "ampliable" in lower or "slot" in lower:
        result["ram_upgradeable"] = True
    else:
        result["ram_upgradeable"] = None  # texto no concluyente o campo no encontrado

    max_ram_m = re.search(r"ampliable hasta (\d+)\s*GB", raw_ram, re.I)
    result["ram_max_gb"] = int(max_ram_m.group(1)) if max_ram_m else None

    return result


KNOWN_BRANDS_RE = re.compile(r"HP|Lenovo|Asus|ASUS|Acer|Dell|MSI|Samsung|Apple|Gigabyte", re.I)


def is_known_brand(name):
    return bool(KNOWN_BRANDS_RE.search(name))


def rating_count_of(p):
    try:
        return int(p.get("rating_count") or 0)
    except (TypeError, ValueError):
        return 0


def risk_level(p):
    """Heurística de riesgo de compra: marca/reseñas/condición/potencia del CPU."""
    s = p["specs"]
    known = is_known_brand(p["name"])
    rc = rating_count_of(p)

    if known and rc >= 50 and s.get("ram_upgradeable") is not False:
        return "Bajo"
    if not known or rc < 5:
        return "Alto"
    return "Medio"


def estimated_lifetime_years(p):
    """Vida útil estimada antes de que el equipo se quede corto para el stack
    de desarrollo, en función de si la RAM se puede ampliar y del riesgo de compra."""
    s = p["specs"]
    risk = p["risk"]
    upgradeable = s.get("ram_upgradeable")

    if risk == "Bajo" and upgradeable is True:
        return 5
    if risk == "Bajo":
        return 4
    if risk == "Medio" and upgradeable is True:
        return 4
    if risk == "Medio":
        return 3
    return 3  # riesgo Alto: marca/ficha poco fiable, o RAM soldada de fábrica


def score_dev_now(product):
    """Rendimiento puro tal cual sale de fábrica hoy: cuánto CPU real (Cinebench
    si lo tenemos), cuánta RAM y SSD trae instalados ahora mismo. No mira
    fiabilidad ni futuro, solo "qué tan rápido es en este momento"."""
    s = product["specs"]
    val = 0.0
    if s["cinebench_r23_multi"]:
        val += s["cinebench_r23_multi"] / 160
    else:
        val += s["cpu_tier"] * 0.7 + s["cpu_power_bonus"] * 1.5
    val += min(s["ram_gb"] or 0, 32) * 1.2
    val += min(s["storage_gb"] or 0, 1024) / 20
    if s["gpu_dedicated"]:
        val += 8
    # el máximo teórico (i9 tope + 32GB + 1TB + gpu dedicada) ronda ~195-200;
    # se reescala a 0-100 en vez de capar en seco, para que sí se note la
    # diferencia entre un i5/U de gama baja y un i7/i9 H de gama alta.
    return round(min(val / 2, 100), 1)


def score_longevity(product):
    """Cuánto va a aguantar sin quedarse corto: RAM ampliable pesa mucho más
    que la RAM instalada hoy, y la generación de CPU indica cuánto le queda
    de vida útil antes de sentirse obsoleto."""
    s = product["specs"]
    val = 30.0  # base

    upgradeable = s.get("ram_upgradeable")
    if upgradeable is True:
        val += 35
        max_ram = s.get("ram_max_gb") or (s.get("ram_gb") or 16)
        val += min(max_ram, 64) * 0.4
    elif upgradeable is False:
        val -= 25
    # upgradeable is None (sin dato): no se suma ni resta, es incertidumbre

    gen = s.get("cpu_gen") or 0
    if s.get("is_amd"):
        if gen >= 7:
            val += 10
        elif gen <= 4 and gen:
            val -= 10
    else:
        if gen >= 13:
            val += 10
        elif gen and gen <= 10:
            val -= 10

    if (s.get("storage_gb") or 0) >= 1024:
        val += 5

    return round(max(0, min(val, 100)), 1)


def score_safe_purchase(product):
    """Qué tan seguro es comprarlo sin sorpresas: marca con trayectoria,
    volumen y nota de reseñas, y si es nuevo o reacondicionado."""
    known = is_known_brand(product["name"])
    rc = rating_count_of(product)
    try:
        rating = float(product.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0

    val = 0.0
    val += 35 if known else 5
    val += rating * 10  # 0-50
    # el volumen de reseñas importa, pero con rendimientos decrecientes
    import math
    val += min(math.log10(rc + 1) * 8, 15)
    if product.get("refurbished"):
        val -= 5

    return round(max(0, min(val, 100)), 1)


def alert_label(p):
    """Etiqueta de alerta automática:
    - NO_COMPRAR: CPU serio (H/HS/HK/HX) pero RAM soldada y tope < 32GB -> se
      queda corto en 1-2 años para este stack, sin forma de corregirlo.
    - INVESTIGAR: i9/HX/HK de gama alta en marca poco conocida -> el chip
      necesita buena refrigeración/VRM que una marca sin trayectoria no
      garantiza; falta información para fiarse a ciegas.
    - COMPRA_SEGURA: CPU de clase H o superior + RAM ampliable + rating >=4.5.
    """
    s = p["specs"]
    power_class = s.get("cpu_power_class")
    is_h_class = power_class in ("H", "HS", "HK", "HX")
    upgradeable = s.get("ram_upgradeable")
    ram_now = s.get("ram_gb") or 0
    ram_ceiling = s.get("ram_max_gb") or ram_now
    try:
        rating = float(p.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0

    if is_h_class and upgradeable is False and ram_ceiling < 32:
        return "NO_COMPRAR"
    if power_class in ("HK", "HX") and not is_known_brand(p["name"]):
        return "INVESTIGAR"
    if is_h_class and upgradeable is True and rating >= 4.5:
        return "COMPRA_SEGURA"
    return None


def main():
    all_products = []
    print(f"Descargando página 1: {BASE_URL}", file=sys.stderr)
    html = fetch(BASE_URL)
    products, total_pages = parse_products(html)
    all_products.extend(products)
    print(f"Total de páginas detectadas: {total_pages}", file=sys.stderr)

    for page in range(2, total_pages + 1):
        url = f"{BASE_URL}&page={page}"
        print(f"Descargando página {page}/{total_pages}...", file=sys.stderr)
        time.sleep(1.5)  # ser respetuoso con el servidor
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  error en página {page}: {e}", file=sys.stderr)
            continue
        products, _ = parse_products(html)
        all_products.extend(products)

    # Deduplicar por SKU
    seen = set()
    deduped = []
    for p in all_products:
        if p["sku"] in seen:
            continue
        seen.add(p["sku"])
        deduped.append(p)

    for p in deduped:
        p["specs"] = extract_specs(p["name"])
        p["refurbished"] = "refurbished" in p["url"]
        p["dev_score"] = score(p)

    deduped.sort(key=lambda p: p["dev_score"], reverse=True)

    # CSV
    with open("laptops.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["dev_score", "name", "price", "cpu", "cpu_gen", "cinebench_r23_multi",
                          "ram_gb", "storage_gb", "screen_in", "gpu_dedicated", "oled",
                          "refurbished", "rating", "rating_count", "url"])
        for p in deduped:
            s = p["specs"]
            writer.writerow([p["dev_score"], p["name"], p["price"], s["cpu"], s["cpu_gen"],
                              s["cinebench_r23_multi"], s["ram_gb"], s["storage_gb"], s["screen_in"],
                              s["gpu_dedicated"], s["oled"], p["refurbished"],
                              p["rating"], p["rating_count"], p["url"]])

    with open("laptops.json", "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    top = deduped[:top_n]

    print(f"\nTotal portátiles únicos encontrados: {len(deduped)}\n")
    print(f"Consultando ficha de producto de los {top_n} mejores (RAM ampliable, "
          f"pantalla, batería)...", file=sys.stderr)
    for i, p in enumerate(top, 1):
        print(f"  {i}/{top_n}: {p['name'][:60]}", file=sys.stderr)
        detail = fetch_product_detail(p["url"])
        p["specs"]["ram_detail"] = detail.get("ram_detail")
        p["specs"]["ram_upgradeable"] = detail.get("ram_upgradeable")
        p["specs"]["ram_max_gb"] = detail.get("ram_max_gb")
        p["specs"]["screen_detail"] = detail.get("screen_detail")
        p["specs"]["battery_detail"] = detail.get("battery_detail")
        p["specs"]["weight_detail"] = detail.get("weight_detail")
        p["risk"] = risk_level(p)
        p["estimated_lifetime_years"] = estimated_lifetime_years(p)
        p["cost_per_year"] = round(p["price"] / p["estimated_lifetime_years"], 2)
        p["alert"] = alert_label(p)
        p["score_dev_now"] = score_dev_now(p)
        p["score_longevity"] = score_longevity(p)
        p["score_safe_purchase"] = score_safe_purchase(p)
        time.sleep(1.2)  # ser respetuoso con el servidor

    with open("laptops_top_enriched.json", "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)

    print(f"\n=== TOP {top_n} para stack Flutter + Claude Code + GoLand ===\n")
    for i, p in enumerate(top, 1):
        s = p["specs"]
        bench = f"CB23:{s['cinebench_r23_multi']}" if s["cinebench_r23_multi"] else "CB23:est."
        cond = "reacondicionado" if p["refurbished"] else "nuevo"
        ram_note = s.get("ram_detail") or "sin dato"
        if s.get("ram_upgradeable") is True:
            ram_note += " [AMPLIABLE]"
        elif s.get("ram_upgradeable") is False:
            ram_note += " [SOLDADA/NO AMPLIABLE]"
        alert_line = f" [{p['alert']}]" if p["alert"] else ""
        print(f"{i}. [{p['dev_score']}] {p['name']} - {p['price']}€ ({cond}) — riesgo: {p['risk']}{alert_line}")
        print(f"   CPU: {s['cpu']} gen/serie {s['cpu_gen']} clase {s['cpu_power_class']} ({bench}) | "
              f"Almacenamiento: {s['storage_gb']}GB SSD | GPU dedicada: {s['gpu_dedicated']} | "
              f"Rating: {p['rating']} ({p['rating_count']})")
        print(f"   RAM: {ram_note}")
        print(f"   Pantalla: {s.get('screen_detail') or 'sin dato'}")
        print(f"   Batería: {s.get('battery_detail') or 'sin dato'} | Peso: {s.get('weight_detail') or 'sin dato'}")
        print(f"   Coste estimado: {p['cost_per_year']}€/año (vida útil estimada: "
              f"{p['estimated_lifetime_years']} años)")
        print(f"   Scores -> desarrollo ahora: {p['score_dev_now']} | longevidad: "
              f"{p['score_longevity']} | compra segura: {p['score_safe_purchase']}")
        print(f"   {p['url']}\n")


if __name__ == "__main__":
    main()
