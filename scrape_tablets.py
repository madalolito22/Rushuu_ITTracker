"""
Scraper de tablets en pccomponentes.com filtradas por precio, pensado para
evaluar cuál es mejor para dibujo/diseño gráfico básico, tomar apuntes en
clase y uso cómodo diario. Android (no hay tablets Windows reales en este
rango de precio en esta tienda: las más baratas empiezan sobre los 390€).

Uso:
    python scrape_tablets.py [TOP_N]

    TOP_N (opcional, por defecto 15): a cuántas de las mejores se les visita
    la ficha de producto para sacar CPU exacta, batería (mAh) y peso real
    (dato que no está en el listado).

Genera:
    tablets.csv / tablets.json -> todas las tablets encontradas con specs
    Imprime el top TOP_N con ficha detallada y "student_score".
"""
import json
import re
import sys
import time
import csv
import urllib.request

BASE_URL = "https://www.pccomponentes.com/tablets?price_from=150&price_to=300"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
}

KNOWN_BRANDS_RE = re.compile(r"Samsung|Lenovo|Xiaomi|Honor|Huawei|Apple", re.I)
STYLUS_RE = re.compile(r"stylus|s\s*pen|l[aá]piz", re.I)
KEYBOARD_RE = re.compile(r"teclado|keyboard", re.I)
CELLULAR_RE = re.compile(r"\b4G\b|\b5G\b", re.I)


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
    screen_m = re.search(r'(\d{1,2}(?:[.,]\d)?)\s*"', name)
    screen_in = float(screen_m.group(1).replace(",", ".")) if screen_m else None

    # Tras el tamaño de pantalla, el patrón habitual es "<RAM>GB <Almacenamiento>GB"
    gb_numbers = [int(n) for n in re.findall(r"(\d{1,3})\s*GB", name, re.I)]
    ram_gb, storage_gb = None, None
    if len(gb_numbers) >= 2:
        ram_gb, storage_gb = gb_numbers[0], gb_numbers[1]
    elif len(gb_numbers) == 1:
        # Ambiguo: podría ser solo almacenamiento anunciado. Se deja como
        # almacenamiento por ser el dato que casi siempre se destaca solo.
        storage_gb = gb_numbers[0]

    return {
        "screen_in": screen_in,
        "ram_gb": ram_gb,
        "storage_gb": storage_gb,
        "stylus_included": bool(STYLUS_RE.search(name)),
        "keyboard_included": bool(KEYBOARD_RE.search(name)),
        "cellular": bool(CELLULAR_RE.search(name)),
    }


def is_known_brand(name):
    return bool(KNOWN_BRANDS_RE.search(name))


def rating_count_of(p):
    try:
        return int(p.get("rating_count") or 0)
    except (TypeError, ValueError):
        return 0


def score(p):
    """Puntuación para uso de estudiante/creativo: dibujo básico, apuntes,
    comodidad de uso diario. No es una tablet de desarrollo: aquí no importa
    el CPU en bruto, importa la pluma, la pantalla y que sea agradable de
    sostener durante horas."""
    s = p["specs"]
    val = 0.0

    # El lápiz es, con diferencia, el factor más importante para dibujo/apuntes.
    if s["stylus_included"]:
        val += 30

    # RAM: Android va sobrado con menos que Windows, pero para no ir justo
    # con apps de notas + navegador + alguna app de dibujo abiertas a la vez.
    ram = s["ram_gb"] or 0
    if ram >= 8:
        val += 20
    elif ram >= 6:
        val += 14
    elif ram >= 4:
        val += 6
    else:
        val += 1

    # Pantalla: tamaño cómodo para dibujar/tomar notas sin ser incómoda de
    # sostener (10-12" es el punto dulce; penaliza extremos).
    screen = s["screen_in"] or 0
    if 10 <= screen <= 12.5:
        val += 12
    elif 9 <= screen < 10 or 12.5 < screen <= 13.5:
        val += 8
    else:
        val += 3

    # Almacenamiento: apuntes, PDFs, apps de dibujo con pinceles/recursos.
    storage = s["storage_gb"] or 0
    if storage >= 256:
        val += 10
    elif storage >= 128:
        val += 8
    elif storage >= 64:
        val += 4
    else:
        val += 1

    # Teclado incluido es un plus para apuntes (opcional, no crítico para dibujo)
    if s["keyboard_included"]:
        val += 4

    # Marca con trayectoria en tablets Android (soporte, actualizaciones, reventa)
    if is_known_brand(p["name"]):
        val += 12
    else:
        val += 2

    # Rating de otros compradores
    try:
        rating = float(p.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    rc = rating_count_of(p)
    val += rating * 3
    val += min(rc / 20, 5)

    return round(val, 2)


DETAIL_FIELD_PATTERNS = {
    "screen_detail": [
        re.compile(r"<strong>Pantalla[^<]*</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Pantalla[^<]*</strong>\s*([^<]*)</li>", re.I),
    ],
    "cpu_detail": [
        re.compile(r"<strong>Procesador</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Procesador</strong>\s*([^<]*)</li>", re.I),
    ],
    "ram_detail": [
        re.compile(r"<strong>Memoria RAM</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Memoria RAM</strong>\s*([^<]*)</li>", re.I),
    ],
    "storage_detail": [
        re.compile(r"<strong>Almacenamiento</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Almacenamiento</strong>\s*([^<]*)</li>", re.I),
    ],
    "battery_detail": [
        re.compile(r"<strong>Bater[^<]*</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Bater[^<]*</strong>\s*([^<]*)</li>", re.I),
    ],
    "weight_detail": [
        re.compile(r"<strong>Peso</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Peso</strong>\s*([^<]*)</li>", re.I),
    ],
    "os_detail": [
        re.compile(r"<strong>Sistema operativo</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Sistema operativo</strong>\s*([^<]*)</li>", re.I),
    ],
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

    weight_m = re.search(r"(\d{2,4})\s*g\b", result.get("weight_detail") or "", re.I)
    result["weight_g"] = int(weight_m.group(1)) if weight_m else None

    return result


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
        p["student_score"] = score(p)

    deduped.sort(key=lambda p: p["student_score"], reverse=True)

    with open("tablets.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["student_score", "name", "price", "screen_in", "ram_gb", "storage_gb",
                          "stylus_included", "keyboard_included", "cellular", "rating",
                          "rating_count", "url"])
        for p in deduped:
            s = p["specs"]
            writer.writerow([p["student_score"], p["name"], p["price"], s["screen_in"],
                              s["ram_gb"], s["storage_gb"], s["stylus_included"],
                              s["keyboard_included"], s["cellular"], p["rating"],
                              p["rating_count"], p["url"]])

    with open("tablets.json", "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    top = deduped[:top_n]

    print(f"\nTotal tablets únicas encontradas: {len(deduped)}\n")
    print(f"Consultando ficha de producto de las {top_n} mejores (CPU, batería, peso)...",
          file=sys.stderr)
    for i, p in enumerate(top, 1):
        print(f"  {i}/{top_n}: {p['name'][:60]}", file=sys.stderr)
        detail = fetch_product_detail(p["url"])
        p["specs"]["screen_detail"] = detail.get("screen_detail")
        p["specs"]["cpu_detail"] = detail.get("cpu_detail")
        p["specs"]["ram_detail"] = detail.get("ram_detail")
        p["specs"]["storage_detail"] = detail.get("storage_detail")
        p["specs"]["battery_detail"] = detail.get("battery_detail")
        p["specs"]["weight_detail"] = detail.get("weight_detail")
        p["specs"]["weight_g"] = detail.get("weight_g")
        p["specs"]["os_detail"] = detail.get("os_detail")
        time.sleep(1.2)

    with open("tablets_top_enriched.json", "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)

    print(f"\n=== TOP {top_n} tablets para dibujo/apuntes/clase ===\n")
    for i, p in enumerate(top, 1):
        s = p["specs"]
        print(f"{i}. [{p['student_score']}] {p['name']} - {p['price']}€")
        print(f"   Pantalla: {s.get('screen_detail') or s['screen_in']} | "
              f"CPU: {s.get('cpu_detail') or 'sin dato'}")
        print(f"   RAM: {s.get('ram_detail') or s['ram_gb']} | "
              f"Almacenamiento: {s.get('storage_detail') or s['storage_gb']}")
        print(f"   Stylus incluido: {s['stylus_included']} | Teclado incluido: {s['keyboard_included']} | "
              f"Conectividad móvil: {s['cellular']}")
        print(f"   Batería: {s.get('battery_detail') or 'sin dato'} | "
              f"Peso: {s.get('weight_detail') or 'sin dato'} | SO: {s.get('os_detail') or 'sin dato'}")
        print(f"   Rating: {p['rating']} ({p['rating_count']})")
        print(f"   {p['url']}\n")


if __name__ == "__main__":
    main()
