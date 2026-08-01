"""
Cruza los resultados de scrape_gpus.py (PCComponentes, nuevas) y
scrape_gpus_cex.py (CeX, segunda mano) por chip, para responder la pregunta
real: para el chip que quiero, ¿compensa pagar más por una nueva con
garantía o me ahorro dinero de verdad comprándola usada?

Requiere haber corrido antes los dos scrapers (gpus.json y cex_gpus.json en
el directorio actual).

Uso:
    python compare_gpus.py

Genera:
    gpu_comparison.csv / gpu_comparison.json -> un chip por fila, mejor
    precio encontrado en cada tienda y el ahorro de ir a la usada.
    Imprime la tabla ordenada por rendimiento relativo.
"""
import json
import csv
import sys


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Falta {path} - corre antes el scraper que lo genera.", file=sys.stderr)
        sys.exit(1)


def best_per_chip(products):
    """Para cada chip se queda con la unidad más barata (la que de verdad
    importa si vas a comprar), no con la de mayor value_score."""
    best = {}
    for p in products:
        chip = p["specs"]["chip"]
        if not chip:
            continue
        price = float(p["price"])
        if chip not in best or price < float(best[chip]["price"]):
            best[chip] = p
    return best


def main():
    pcc = load("gpus.json")
    cex = load("cex_gpus.json")

    best_pcc = best_per_chip(pcc)
    best_cex = best_per_chip(cex)

    chips = set(best_pcc) | set(best_cex)
    rows = []
    for chip in chips:
        new = best_pcc.get(chip)
        used = best_cex.get(chip)
        relative_perf = (new or used)["specs"]["relative_perf"]
        new_price = float(new["price"]) if new else None
        used_price = float(used["price"]) if used else None
        if new_price and used_price:
            ahorro_pct = round((1 - used_price / new_price) * 100, 1)
            mejor_opcion = "CeX (usada)" if used_price < new_price else "PCComponentes (nueva)"
        else:
            ahorro_pct = None
            mejor_opcion = "CeX (usada)" if used and not new else "PCComponentes (nueva)"
        rows.append({
            "chip": chip,
            "relative_perf": relative_perf,
            "pccomponentes_price": new_price,
            "pccomponentes_url": new["url"] if new else None,
            "cex_price": used_price,
            "cex_url": used["url"] if used else None,
            "ahorro_pct_usada": ahorro_pct,
            "mejor_opcion": mejor_opcion,
        })

    rows.sort(key=lambda r: (r["relative_perf"] or 0), reverse=True)

    with open("gpu_comparison.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["chip", "relative_perf", "pccomponentes_price", "cex_price",
                          "ahorro_pct_usada", "mejor_opcion", "pccomponentes_url", "cex_url"])
        for r in rows:
            writer.writerow([r["chip"], r["relative_perf"], r["pccomponentes_price"],
                              r["cex_price"], r["ahorro_pct_usada"], r["mejor_opcion"],
                              r["pccomponentes_url"], r["cex_url"]])

    with open("gpu_comparison.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    print(f"\n{len(rows)} chips distintos comparados "
          f"({len(best_pcc)} en PCComponentes, {len(best_cex)} en CeX, "
          f"{len(set(best_pcc) & set(best_cex))} en ambas)\n")
    print(f"{'Chip':<16}{'Perf':>6}  {'PCC nueva':>12}  {'CeX usada':>12}  {'Ahorro':>8}  Mejor opción")
    for r in rows:
        pcc_str = f"{r['pccomponentes_price']:.0f}€" if r["pccomponentes_price"] else "-"
        cex_str = f"{r['cex_price']:.0f}€" if r["cex_price"] else "-"
        ahorro_str = f"{r['ahorro_pct_usada']:.0f}%" if r["ahorro_pct_usada"] is not None else "-"
        print(f"{r['chip']:<16}{r['relative_perf'] or 0:>6}  {pcc_str:>12}  {cex_str:>12}  "
              f"{ahorro_str:>8}  {r['mejor_opcion']}")


if __name__ == "__main__":
    main()
