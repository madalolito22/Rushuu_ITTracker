"""
Scraper de mini PCs en pccomponentes.com filtrados por precio, pensado para
evaluar cuál es mejor como home-server 24/7: Jellyfin (con transcodificación
por hardware), Docker/contenedores varios y, si el equipo da para ello,
inferencia local de LLMs pequeños/medianos (Ollama/llama.cpp).

Uso:
    python scrape_minipcs.py [TOP_N]

    TOP_N (opcional, por defecto 15): a cuántos de los mejores clasificados
    se les visita además su ficha de producto para sacar RAM ampliable o
    soldada, conectividad (red 2.5GbE) y consumo/TDP (dato que no está en
    el listado).

Genera:
    minipcs.csv / minipcs.json -> todos los mini PCs encontrados con specs
    minipcs_top_enriched.json  -> el top TOP_N con la ficha completa
    Imprime el top TOP_N con ficha detallada y "homelab_score".
"""
import json
import re
import sys
import time
import csv

from http_fetch import fetch

# Sin cota inferior a propósito: si hay algo bueno y más barato que el tope,
# mejor. El tope es el presupuesto real de la compra.
BASE_URL = "https://www.pccomponentes.com/categorias/mini-pcs?price_to=800"

# Tier de CPU por familia/gama. Deliberadamente los N-series (N100/N150/N200/
# N305) NO se meten en el mismo saco que Celeron/Pentium de gama laptop barata:
# en un mini PC de home-server su bajo consumo es una ventaja (24/7 encendido)
# y traen una iGPU con Quick Sync perfectamente capaz para Jellyfin, así que se
# quedan en un escalón propio en vez de en el más bajo. El resto de la
# diferencia de "cuánto homelab aguanta" la aporta la RAM y el bonus de iGPU,
# no este tier.
CPU_TIERS = [
    (re.compile(r"Ryzen\s*AI\s*Max|Ryzen\s*(?:AI\s*)?9|Core\s*i9|Core\s*Ultra\s*9", re.I), 90),
    (re.compile(r"Ryzen\s*(?:AI\s*)?7|Core\s*i7|Core\s*Ultra\s*7", re.I), 78),
    (re.compile(r"Ryzen\s*(?:AI\s*)?5|Core\s*i5|Core\s*Ultra\s*5", re.I), 60),
    (re.compile(r"Ryzen\s*(?:AI\s*)?3|Core\s*i3", re.I), 35),
    (re.compile(r"N3(05|00)|N2(00)|N1(50|00)|N95\b", re.I), 32),
    (re.compile(r"Celeron|Pentium", re.I), 18),
]

# Ryzen AI Max ("Strix Halo": 375/380/385/390/395) es una arquitectura aparte:
# memoria unificada LPDDR5X soldada pero con un ancho de banda muy por encima
# de un DDR5 SO-DIMM normal, con iGPU (Radeon 8060S) capaz de tirar de modelos
# grandes. Es, hoy por hoy, la opción de referencia para IA local en un mini
# PC, así que se marca aparte y se premia con un bonus plano en vez de tratar
# su RAM soldada como una señal negativa (ver alert_label).
STRIX_HALO_RE = re.compile(r"Ryzen\s*AI\s*Max", re.I)

NPU_RE = re.compile(r"\bNPU\b|Ryzen\s*AI|Core\s*Ultra|Copilot\+?", re.I)
DEDICATED_GPU_RE = re.compile(r"RTX\s*\d{4}|GTX\s*\d{4}|Arc\s*A\d{3}", re.I)
LAN_2_5G_RE = re.compile(r"2[.,]5\s*G(?:b(?:it|E)?)?\b", re.I)

# Serie AMD clásica (primer dígito del modelo de 4-5 cifras, ej. "7735HS" ->
# serie 7000) para saber si el VCN (motor de vídeo) es reciente. Solo aplica
# a la nomenclatura antigua "Ryzen N nnnnXX": la nueva "Ryzen AI 9 HX 370" /
# "Ryzen AI 7 350" reinicia la numeración a 3 cifras y SIEMPRE es reciente
# (RDNA3.5, ver RYZEN_AI_RE más abajo), así que no debe pasar por este regex
# o el "3" de "370" se leería como una serie 3000 antigua.
AMD_SERIES_RE = re.compile(r"Ryzen\s*[3579]\D{0,4}(\d)\d{3}", re.I)

# Ryzen AI (no Max) - series 200/300: "Ryzen AI 9 HX 370", "Ryzen AI 7 350",
# etc. Arquitectura RDNA3.5 moderna, igual de válida que una serie 7000/8000
# clásica para el bonus de VCN, pero con una numeración que no sigue el
# patrón de AMD_SERIES_RE.
RYZEN_AI_RE = re.compile(r"Ryzen\s*AI\s*(?!Max)", re.I)

INTEL_FAMILY_RE = re.compile(r"Intel|Core|N3(05|00)|N2(00)|N1(50|00)|N95\b|Celeron|Pentium", re.I)
AMD_FAMILY_RE = re.compile(r"Ryzen|AMD", re.I)


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
    # RAM vs. almacenamiento: mismo patrón que scrape_laptops.py (el
    # almacenamiento casi siempre va como "<n>GB SSD" o "<n>TB SSD" y la RAM
    # es el número GB suelto que queda antes).
    storage = None
    storage_m = re.search(r"(\d+)\s*(GB|TB)\s*SSD", name, re.I)
    if storage_m:
        val = int(storage_m.group(1))
        storage = val * 1024 if storage_m.group(2).upper() == "TB" else val

    ram = None
    ram_candidates = re.findall(r"(\d{1,3})\s*GB", name, re.I)
    if storage_m and ram_candidates:
        storage_val_str = storage_m.group(1)
        ram_candidates_wo_storage = [c for c in ram_candidates if c != storage_val_str]
        ram = int(ram_candidates_wo_storage[0]) if ram_candidates_wo_storage else (
            int(ram_candidates[0]) if len(ram_candidates) == 1 else None
        )
    elif ram_candidates:
        ram = int(ram_candidates[0])

    cpu = None
    cpu_tier = 0
    for pattern, tier in CPU_TIERS:
        m = pattern.search(name)
        if m:
            cpu_tier = tier
            cpu = m.group(0)
            break

    strix_halo = bool(STRIX_HALO_RE.search(name))
    npu = bool(NPU_RE.search(name))
    dedicated_gpu = bool(DEDICATED_GPU_RE.search(name))
    lan_2_5g = bool(LAN_2_5G_RE.search(name))

    is_amd = bool(AMD_FAMILY_RE.search(name))
    is_intel = bool(INTEL_FAMILY_RE.search(name)) and not is_amd

    igpu_vendor = "amd" if is_amd else ("intel" if is_intel else None)

    amd_series = None
    ryzen_ai = False
    if is_amd:
        ryzen_ai = bool(RYZEN_AI_RE.search(name))
        if not ryzen_ai and not strix_halo:
            series_m = AMD_SERIES_RE.search(name)
            if series_m:
                amd_series = int(series_m.group(1))

    # VCN moderno (RDNA3+): Strix Halo, Ryzen AI 200/300 (nomenclatura nueva)
    # o serie clásica 7000/8000+ (nomenclatura "Ryzen N nnnnXX").
    amd_modern_vcn = strix_halo or ryzen_ai or (amd_series or 0) >= 7

    return {
        "ram_gb": ram,
        "storage_gb": storage,
        "cpu": cpu,
        "cpu_tier": cpu_tier,
        "strix_halo": strix_halo,
        "npu": npu,
        "dedicated_gpu": dedicated_gpu,
        "lan_2_5g": lan_2_5g,
        "igpu_vendor": igpu_vendor,
        "amd_modern_vcn": amd_modern_vcn,
        "amd_series": amd_series,
    }


def score(product):
    """Puntuación para home-server (Jellyfin + Docker) con margen para IA
    local si el equipo da para ello. No es una puntuación de "PC más potente
    por el precio" a secas: la RAM y la transcodificación por hardware pesan
    más que el tier de CPU en bruto, porque son las que de verdad limitan
    cuántos contenedores/usuarios de Jellyfin en paralelo aguanta el equipo."""
    s = product["specs"]
    val = 0.0

    # RAM: el factor dominante. Más contenedores a la vez y, sobre todo,
    # techo de tamaño de modelo para IA local (7-8B necesita ~8-16GB con
    # cuantización, 13-20B ya pide 32GB, 30B+ solo es viable con 64GB+).
    ram = s["ram_gb"] or 0
    if ram >= 64:
        val += 40
    elif ram >= 32:
        val += 32
    elif ram >= 16:
        val += 20
    elif ram >= 8:
        val += 8
    else:
        val += 1

    # Ryzen AI Max ("Strix Halo"): memoria unificada de alto ancho de banda +
    # iGPU potente, hoy por hoy la mejor opción de IA local en formato mini
    # PC. Bonus aparte porque no lo captura ni el tier de CPU ni la RAM sola.
    if s["strix_halo"]:
        val += 25

    # NPU (Ryzen AI / Core Ultra / Copilot+): bonus moderado, no dominante,
    # porque el aprovechamiento real de la NPU en Linux/Ollama todavía es
    # limitado en la práctica (la mayoría de la inferencia sigue cayendo en
    # CPU/iGPU aunque el chip tenga NPU).
    if s["npu"] and not s["strix_halo"]:
        val += 10

    # CPU en bruto: importa para Docker/contenedores varios corriendo a la
    # vez, pero pesa menos que en un portátil de desarrollo porque las
    # cargas de un home-server suelen estar más limitadas por RAM/IO que
    # por CPU.
    val += s["cpu_tier"] * 0.25

    # Transcodificación por hardware para Jellyfin: Quick Sync (Intel) es el
    # estándar de facto por compatibilidad de códecs y madurez de drivers en
    # Linux/VAAPI, incluidos los N100/N305 pese a su CPU floja. El VCN de AMD
    # en series recientes (7000/8000/IA, RDNA3+) ya es sólido; en series
    # antiguas es más flojo/menos probado.
    if s["igpu_vendor"] == "intel":
        val += 18
    elif s["igpu_vendor"] == "amd":
        val += 14 if s["amd_modern_vcn"] else 8

    # GPU dedicada (rara en este formato, pero existe): aporta CUDA/aceleración
    # real para IA además de transcodificación, así que suma aparte.
    if s["dedicated_gpu"]:
        val += 10

    # Almacenamiento
    storage = s["storage_gb"] or 0
    if storage >= 1024:
        val += 8
    elif storage >= 512:
        val += 6
    elif storage >= 256:
        val += 3
    else:
        val += 1

    # Red 2.5GbE: relevante para mover archivos grandes/streaming en un
    # home-server (NAS, biblioteca de Jellyfin en la propia red).
    if s["lan_2_5g"]:
        val += 6

    # Rating de otros compradores como desempate suave
    try:
        rating = float(product.get("rating") or 0)
        val += rating * 2
    except (TypeError, ValueError):
        pass

    return round(val, 2)


# Campos de la tabla de specs de la ficha de producto que no están en el
# listado y solo se pueden sacar visitando cada mini PC individualmente.
# Varias etiquetas por campo porque cada fabricante (GMKtec, Beelink,
# Minisforum, ASUS...) redacta su ficha con nombres ligeramente distintos.
DETAIL_FIELD_PATTERNS = {
    "ram_detail": [
        re.compile(r"<strong>Memoria RAM</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Memoria RAM</strong>\s*([^<]*)</li>", re.I),
    ],
    "connectivity_detail": [
        re.compile(r"<strong>Conectividad</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Conectividad</strong>\s*([^<]*)</li>", re.I),
        re.compile(r"<strong>Red</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Red</strong>\s*([^<]*)</li>", re.I),
    ],
    "consumption_detail": [
        re.compile(r"<strong>Consumo</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>Consumo</strong>\s*([^<]*)</li>", re.I),
        re.compile(r"<strong>TDP</strong></td>\s*<td>([^<]*)</td>", re.I),
        re.compile(r"<strong>TDP</strong>\s*([^<]*)</li>", re.I),
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

    raw_ram = result.get("ram_detail") or ""
    lower = raw_ram.lower()
    if "no ampliable" in lower or "soldada" in lower or "soldado" in lower or "integrada" in lower:
        result["ram_upgradeable"] = False
    elif "ampliable" in lower or "slot" in lower:
        result["ram_upgradeable"] = True
    else:
        result["ram_upgradeable"] = None

    max_ram_m = re.search(r"ampliable hasta (\d+)\s*GB", raw_ram, re.I)
    result["ram_max_gb"] = int(max_ram_m.group(1)) if max_ram_m else None

    result["lan_2_5g_detail"] = bool(LAN_2_5G_RE.search(result.get("connectivity_detail") or ""))

    return result


# Marcas de fiar en el nicho de mini PCs: mezcla de OEMs generalistas y las
# marcas "boutique" (mayoritariamente chinas) que, en ESTE mercado concreto,
# sí tienen trayectoria y volumen de reseñas reales (al revés que en
# portátiles, aquí GMKtec/Beelink/Minisforum SON la referencia, no una señal
# de alarma).
KNOWN_BRANDS_RE = re.compile(
    r"ASUS|HP|Lenovo|Dell|MSI|Intel|Zotac|Beelink|GMKtec|Minisforum|Geekom|"
    r"Trigkey|Aoostar|Chuwi|Acemagic",
    re.I,
)


def is_known_brand(name):
    return bool(KNOWN_BRANDS_RE.search(name))


def rating_count_of(p):
    try:
        return int(p.get("rating_count") or 0)
    except (TypeError, ValueError):
        return 0


def risk_level(p):
    s = p["specs"]
    known = is_known_brand(p["name"])
    rc = rating_count_of(p)

    if known and rc >= 20 and s.get("ram_upgradeable") is not False:
        return "Bajo"
    if not known or rc < 5:
        return "Alto"
    return "Medio"


def alert_label(p):
    """Etiqueta de alerta automática:
    - NO_COMPRAR: RAM soldada con techo bajo (<=16GB) -> se queda corto para
      Docker+Jellyfin+algo de IA local en poco tiempo, sin forma de ampliarlo.
      No se aplica a equipos con Strix Halo/64GB+ de fábrica: ahí la RAM
      soldada es una decisión de arquitectura (ancho de banda), no un límite.
    - INVESTIGAR: specs top (NPU/Strix Halo/32GB+) en una marca sin
      trayectoria ni reseñas -> puede ser un clon/rebranding sin garantías
      reales detrás, conviene mirar reviews independientes antes de comprar.
    - COMPRA_SEGURA: marca conocida + RAM ampliable (o ya generosa de
      fábrica) + rating alto.
    """
    s = p["specs"]
    upgradeable = s.get("ram_upgradeable")
    ram_now = s["ram_gb"] or 0
    ram_ceiling = s.get("ram_max_gb") or ram_now
    known = is_known_brand(p["name"])
    try:
        rating = float(p.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0

    if upgradeable is False and ram_ceiling <= 16 and not s["strix_halo"]:
        return "NO_COMPRAR"
    if not known and (s["strix_halo"] or s["npu"] or ram_now >= 32):
        return "INVESTIGAR"
    if known and (upgradeable is True or ram_now >= 32) and rating >= 4.5:
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
        p["refurbished"] = "refurbished" in p["url"]
        p["homelab_score"] = score(p)

    deduped.sort(key=lambda p: p["homelab_score"], reverse=True)

    with open("minipcs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["homelab_score", "name", "price", "cpu", "ram_gb", "storage_gb",
                          "igpu_vendor", "strix_halo", "npu", "dedicated_gpu", "lan_2_5g",
                          "refurbished", "rating", "rating_count", "url"])
        for p in deduped:
            s = p["specs"]
            writer.writerow([p["homelab_score"], p["name"], p["price"], s["cpu"], s["ram_gb"],
                              s["storage_gb"], s["igpu_vendor"], s["strix_halo"], s["npu"],
                              s["dedicated_gpu"], s["lan_2_5g"], p["refurbished"],
                              p["rating"], p["rating_count"], p["url"]])

    with open("minipcs.json", "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    top = deduped[:top_n]

    print(f"\nTotal mini PCs únicos encontrados: {len(deduped)}\n")
    print(f"Consultando ficha de producto de los {top_n} mejores (RAM ampliable, "
          f"conectividad, consumo)...", file=sys.stderr)
    for i, p in enumerate(top, 1):
        print(f"  {i}/{top_n}: {p['name'][:60]}", file=sys.stderr)
        detail = fetch_product_detail(p["url"])
        p["specs"]["ram_detail"] = detail.get("ram_detail")
        p["specs"]["ram_upgradeable"] = detail.get("ram_upgradeable")
        p["specs"]["ram_max_gb"] = detail.get("ram_max_gb")
        p["specs"]["connectivity_detail"] = detail.get("connectivity_detail")
        p["specs"]["consumption_detail"] = detail.get("consumption_detail")
        if detail.get("lan_2_5g_detail"):
            p["specs"]["lan_2_5g"] = True
        p["risk"] = risk_level(p)
        p["alert"] = alert_label(p)
        time.sleep(1.2)

    with open("minipcs_top_enriched.json", "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)

    print(f"\n=== TOP {top_n} mini PCs para IA local / home-server / Jellyfin ===\n")
    for i, p in enumerate(top, 1):
        s = p["specs"]
        cond = "reacondicionado" if p["refurbished"] else "nuevo"
        ram_note = s.get("ram_detail") or (f"{s['ram_gb']}GB" if s.get("ram_gb") else "sin dato")
        if s.get("ram_upgradeable") is True:
            ram_note += " [AMPLIABLE]"
        elif s.get("ram_upgradeable") is False:
            ram_note += " [SOLDADA/NO AMPLIABLE]"
        extras = []
        if s["strix_halo"]:
            extras.append("Ryzen AI Max (Strix Halo)")
        if s["npu"] and not s["strix_halo"]:
            extras.append("NPU")
        if s["dedicated_gpu"]:
            extras.append("GPU dedicada")
        if s["lan_2_5g"]:
            extras.append("2.5GbE")
        extras_note = f" | {', '.join(extras)}" if extras else ""
        alert_line = f" [{p['alert']}]" if p["alert"] else ""
        print(f"{i}. [{p['homelab_score']}] {p['name']} - {p['price']}€ ({cond}) — "
              f"riesgo: {p['risk']}{alert_line}")
        print(f"   CPU: {s['cpu'] or 'sin dato'} | iGPU: {s['igpu_vendor'] or 'sin dato'} | "
              f"Almacenamiento: {s['storage_gb']}GB SSD{extras_note}")
        print(f"   RAM: {ram_note}")
        print(f"   Conectividad: {s.get('connectivity_detail') or 'sin dato'}")
        print(f"   Consumo: {s.get('consumption_detail') or 'sin dato'}")
        print(f"   Rating: {p['rating']} ({p['rating_count']})")
        print(f"   {p['url']}\n")


if __name__ == "__main__":
    main()
