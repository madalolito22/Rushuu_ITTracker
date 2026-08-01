"""
Scraper de tarjetas gráficas de segunda mano/reacondicionadas en es.webuy.com
(CeX), pensado para comparar contra scrape_gpus.py (PCComponentes, nuevas) y
ver si compensa más pagar de más por una nueva o comprar usada más barata.

CeX es una SPA (Nuxt) que no incrusta los productos en el HTML: los pide en
el cliente a su buscador (Algolia) igual que hace pccomponentes.com con su
bloque JSON-LD, solo que aquí el "documento embebido" es una llamada JSON.
Los credenciales de Algolia (app id + api key de solo-búsqueda) los publica
la propia web en un endpoint de configuración público
(v3/appsettings/prelogin) que carga cualquier visitante antes de buscar, así
que se piden en caliente en vez de fijarlos aquí por si rotan.

Uso:
    python scrape_gpus_cex.py [TOP_N]

Genera:
    cex_gpus.csv / cex_gpus.json -> todas las gráficas encontradas con specs
    Imprime el top TOP_N por "value_score".
"""
import json
import sys
import time
import csv
import urllib.request

from gpu_common import extract_specs, is_known_brand

APPSETTINGS_URL = "https://wss2.cex.es.webuy.io/v3/appsettings/prelogin?platformId=18"
# "Tarjetas Graficas - PCi-E", visible en la URL de búsqueda de la propia web
# (es.webuy.com/search?categoryIds=887&categoryName=...).
CATEGORY_ID = "887"
PRODUCT_URL = "https://es.webuy.com/product-detail?id="
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Content-Type": "application/json",
}
# Filtro replicado del que aplica la web por defecto (oculta agotados,
# descatalogados y variantes no visibles): sin esto Algolia devuelve ~5x más
# resultados de los que muestra realmente la búsqueda.
LISTING_FILTERS = (
    "boxDeleted=0 AND showOnWeb=1 AND discontinued=0 AND webSaleAllowed=1 "
    "AND boxWebSaleAllowed=1 AND boxVisibilityOnWeb=1 AND inStockOnline=1"
)


def fetch(url, retries=4, data=None):
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=HEADERS, data=data)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_err = e
            time.sleep(2 + attempt * 3)
    raise last_err


def get_algolia_credentials():
    html = fetch(APPSETTINGS_URL)
    settings = json.loads(html)["response"]["data"]["preLoginSettings"]
    return {
        "app_id": settings["algoliaAppId"],
        "api_key": settings["algoliaSearchAppKey"],
        "index_name": settings["algoliaIndexName"],
    }


def algolia_search(creds, page, hits_per_page=1000):
    url = (
        f"https://search.webuy.io/1/indexes/*/queries"
        f"?x-algolia-application-id={creds['app_id']}&x-algolia-api-key={creds['api_key']}"
    )
    params = (
        f"query=&page={page}&hitsPerPage={hits_per_page}"
        f'&facetFilters=[["categoryId:{CATEGORY_ID}"]]'
        f"&filters={LISTING_FILTERS}"
    )
    body = json.dumps({"requests": [{"indexName": creds["index_name"], "params": params}]}).encode("utf-8")
    resp = json.loads(fetch(url, data=body))
    return resp["results"][0]


def rating_of(p):
    try:
        return float(p.get("rating") or 0)
    except (TypeError, ValueError):
        return 0.0


def score(p):
    """Puntuación de valor real para una gráfica de segunda mano: rendimiento
    por euro, igual que en pccomponentes, pero sin el respaldo de "producto
    nuevo con garantía de fábrica" — aquí el stock disponible y la valoración
    de otros compradores pesan más porque son la única señal de que el
    vendedor (CeX) no se está quedando con un lote problemático."""
    s = p["specs"]
    price = float(p["price"])

    if not s["relative_perf"]:
        return 1.0

    perf_per_100eur = s["relative_perf"] / (price / 100)
    val = perf_per_100eur * 10

    vram = s["vram_gb"] or 0
    if vram >= 16:
        val += 10
    elif vram >= 12:
        val += 7
    elif vram == 8:
        val += 2
    else:
        val += 0

    if is_known_brand(p["name"]):
        val += 8
    else:
        val += 1

    val += rating_of(p) * 3

    # Stock alto en una tienda de segunda mano suele significar "modelo
    # popular que rota bien", no un lote defectuoso que nadie compra.
    stock = int(p.get("ecom_quantity") or 0)
    val += min(stock / 5, 4)

    return round(val, 2)


def alert_of(p):
    s = p["specs"]
    if not s["relative_perf"]:
        return "SIN_DATO_BENCHMARK"
    if (s["vram_gb"] or 0) <= 8 and s["relative_perf"] >= 70:
        return "INVESTIGAR"
    if is_known_brand(p["name"]) and rating_of(p) >= 4.0 and int(p.get("ecom_quantity") or 0) >= 3:
        return "COMPRA_SEGURA"
    return ""


def main():
    print("Consultando credenciales públicas de búsqueda de CeX...", file=sys.stderr)
    creds = get_algolia_credentials()

    all_hits = []
    page = 0
    while True:
        print(f"Descargando página {page + 1}...", file=sys.stderr)
        result = algolia_search(creds, page)
        all_hits.extend(result["hits"])
        if (page + 1) * result.get("hitsPerPage", len(result["hits"])) >= result.get("nbHits", 0):
            break
        page += 1
        time.sleep(0.5)

    products = []
    for hit in all_hits:
        products.append({
            "name": hit["boxName"],
            "sku": hit["boxId"],
            "url": PRODUCT_URL + hit["boxId"],
            "price": hit["sellPrice"],
            "rating": hit.get("rating"),
            "ecom_quantity": hit.get("ecomQuantity"),
            "in_store_count": len(hit.get("stores") or []),
        })

    for p in products:
        p["specs"] = extract_specs(p["name"])
        p["value_score"] = score(p)
        p["alerta"] = alert_of(p)

    products.sort(key=lambda p: p["value_score"], reverse=True)

    with open("cex_gpus.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["value_score", "alerta", "name", "price", "chip", "vram_gb",
                          "relative_perf", "rating", "ecom_quantity", "in_store_count", "url"])
        for p in products:
            s = p["specs"]
            writer.writerow([p["value_score"], p["alerta"], p["name"], p["price"],
                              s["chip"], s["vram_gb"], s["relative_perf"], p["rating"],
                              p["ecom_quantity"], p["in_store_count"], p["url"]])

    with open("cex_gpus.json", "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    top = products[:top_n]

    print(f"\nTotal gráficas de segunda mano encontradas: {len(products)}\n")
    print(f"=== TOP {top_n} tarjetas gráficas CeX por rendimiento/precio ===\n")
    for i, p in enumerate(top, 1):
        s = p["specs"]
        print(f"{i}. [{p['value_score']}] {p['alerta'] or '-'} {p['name']} - {p['price']}€")
        print(f"   Chip: {s['chip'] or 'sin dato'} | "
              f"Rendimiento relativo (RTX4090=100): {s['relative_perf'] or 'sin dato'} | "
              f"VRAM: {s['vram_gb'] or 'sin dato'}GB")
        print(f"   Rating: {p['rating']} | Stock online: {p['ecom_quantity']} | "
              f"Disponible en {p['in_store_count']} tiendas físicas")
        print(f"   {p['url']}\n")


if __name__ == "__main__":
    main()
