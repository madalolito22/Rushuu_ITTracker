# PCComponentes Scrapers

Scrapers en Python para listados de tiendas (actualmente [pccomponentes.com](https://www.pccomponentes.com) y [es.webuy.com](https://es.webuy.com) / CeX), pensados no solo para extraer precio/nombre, sino para **puntuar los productos según un caso de uso concreto** (desarrollo de software, dibujo/apuntes, rendimiento/precio real en GPUs, etc.) en vez de ordenar por "ficha técnica" a secas. Para las tarjetas gráficas, además se puede cruzar nuevo (PCComponentes) contra segunda mano (CeX) — ver `compare_gpus.py`.

Construido de forma iterativa con [Claude Code](https://claude.com/claude-code): cada regla de scoring, penalización o columna nueva salió de comparar los resultados contra la realidad (reseñas de compradores, benchmarks reales de CPU, fichas técnicas completas) y corregir cuando el número no cuadraba con el sentido común.

## Scripts disponibles

### `scrape_laptops.py` — portátiles para desarrollo (Flutter/GoLand/Docker/IA)

```bash
python scrape_laptops.py [TOP_N]
```

- `TOP_N` (opcional, por defecto 15): a cuántos de los mejores clasificados se les visita además su ficha de producto para sacar datos que **no** están en el listado (RAM ampliable o soldada, pantalla, batería, peso).
- La URL base y el rango de precio se configuran en `BASE_URL` al principio del archivo.

Qué hace:
1. **Fase 1 (listado completo):** recorre todas las páginas del filtro, extrae nombre/precio/URL/rating desde el bloque `<script type="application/ld+json" id="microdata-product-list-script">` que la propia web incrusta (mucho más estable que parsear clases CSS), y saca del nombre del producto: CPU, generación/serie, clase de potencia del chip (U/P/H/HS/HX/HK), RAM, almacenamiento, pantalla, GPU dedicada.
2. **Scoring con benchmark real:** cuando el modelo de CPU tiene un valor verificado en `CINEBENCH_R23_MULTI` (promedios de Notebookcheck), se usa directamente en vez de una heurística por "i7 vs i5".
3. **Fase 2 (solo top N):** visita la ficha de cada uno de los mejores y extrae de la tabla de especificaciones: RAM ampliable/soldada (y máximo oficial), pantalla, batería, peso.
4. **Señales derivadas:**
   - `riesgo` (Bajo/Medio/Alto): marca conocida + volumen de reseñas + condición (nuevo/reacondicionado).
   - `alerta`: `NO_COMPRAR` (CPU potente pero RAM soldada con techo bajo), `INVESTIGAR` (CPU muy potente en marca sin trayectoria — riesgo térmico/VRM sin garantías), `COMPRA_SEGURA` (CPU serio + RAM ampliable + rating alto).
   - `coste_por_año`: precio ÷ vida útil estimada (que depende de si la RAM es ampliable y del riesgo de compra) — para que "más barato" no gane automáticamente si se queda obsoleto antes.
   - Tres scores separados en vez de uno solo: `score_dev_now` (rendimiento tal cual sale de fábrica), `score_longevity` (cuánto va a aguantar), `score_safe_purchase` (cuánto riesgo real hay en la compra). Esto evita que una "bestia de laboratorio" (ej. un i9 en una marca desconocida) gane siempre solo por tener el benchmark más alto.

Salida: `laptops.csv`, `laptops.json` (todos los productos) y `laptops_top_enriched.json` (el top N con la ficha completa).

### `scrape_tablets.py` — tablets para dibujo básico / apuntes / clase

```bash
python scrape_tablets.py [TOP_N]
```

Mismo patrón de dos fases que el de portátiles, pero con un `student_score` distinto pensado para uso creativo/estudiantil en vez de desarrollo:
- El lápiz incluido (`stylus_included`) pesa más que cualquier otra cosa.
- RAM/almacenamiento con umbrales mucho más bajos (Android no necesita lo que necesita GoLand+Docker+un emulador).
- Tamaño de pantalla con un "punto dulce" (10-12,5") en vez de "cuanto más grande mejor".
- Marca conocida en tablets Android (Samsung, Lenovo, Xiaomi, Honor, Huawei, Apple) como señal de soporte/actualizaciones.

Salida: `tablets.csv`, `tablets.json`, `tablets_top_enriched.json`.

### `scrape_gpus.py` — tarjetas gráficas por rendimiento/precio real

```bash
python scrape_gpus.py [TOP_N]
```

Mismo patrón de dos fases que los anteriores, pero con un `value_score` centrado en rendimiento real por euro en vez de núcleos/GB en bruto:
- `GPU_RELATIVE_PERFORMANCE`: índice de rendimiento relativo por modelo de chip exacto (RTX 4090 = 100), agregado de benchmarks públicos en vez de comparar "más núcleos = mejor".
- El score central es rendimiento relativo ÷ precio; VRAM y marca conocida suman como señales secundarias, no como el factor principal.
- `alerta`: `SIN_DATO_BENCHMARK` (chip no reconocido, no se puede puntuar con fiabilidad), `INVESTIGAR` (gráfica potente con poca VRAM, puede quedarse corta), `COMPRA_SEGURA` (marca conocida + volumen de reseñas alto).
- Las etiquetas de la ficha de producto (TDP, bus de memoria, etc.) varían según fabricante (ej. Palit usa "Consumo"/"Ancho de bus" donde otros usan "TDP"/"Bus de memoria"); el scraper prueba varias etiquetas conocidas por campo.

Salida: `gpus.csv`, `gpus.json`, `gpus_top_enriched.json`.

### `scrape_gpus_cex.py` — tarjetas gráficas de segunda mano/reacondicionadas en CeX

```bash
python scrape_gpus_cex.py [TOP_N]
```

Mismo criterio de valor (rendimiento relativo ÷ precio) que `scrape_gpus.py`, aplicado al catálogo de segunda mano de [es.webuy.com](https://es.webuy.com). CeX es una SPA que no incrusta los productos en el HTML (a diferencia de pccomponentes): los pide en el cliente a su buscador (Algolia). El scraper replica esa llamada con `urllib` en vez de parsear HTML:

1. Pide la configuración pública de búsqueda (`v3/appsettings/prelogin`) para sacar el app id y la api key de solo-búsqueda de Algolia (públicos, los usa cualquier visitante; se piden en caliente por si rotan en vez de fijarlos en el código).
2. Consulta el índice de Algolia filtrando por `categoryId:887` (Tarjetas Gráficas - PCi-E) con los mismos filtros que aplica la propia web por defecto (oculta agotados/descatalogados — sin esto Algolia devuelve ~5x más resultados de los que muestra la búsqueda real).
3. Parsea chip/VRAM del nombre igual que con pccomponentes (mismo `CHIP_RE`, ver `gpu_common.py`) y puntúa con rating + stock disponible en vez de nº de reseñas (CeX no expone ese dato) — en second-hand, un modelo con stock alto en varias tiendas es señal de que rota bien, no de que sea un lote problemático.

Salida: `cex_gpus.csv`, `cex_gpus.json`.

### `compare_gpus.py` — cruce PCComponentes (nueva) vs. CeX (usada)

```bash
python compare_gpus.py
```

Requiere haber corrido antes `scrape_gpus.py` y `scrape_gpus_cex.py` (usa `gpus.json` y `cex_gpus.json`). Por cada chip encontrado en cualquiera de las dos tiendas, coge el precio más barato disponible en cada una y calcula el ahorro real de ir a la usada — para decidir con datos si compensa pagar de más por la garantía de fábrica o no.

Salida: `gpu_comparison.csv`, `gpu_comparison.json`.

## `gpu_common.py` — piezas compartidas

`scrape_gpus.py` y `scrape_gpus_cex.py` comparten el parseo de chip/VRAM y la tabla de rendimiento relativo (`GPU_RELATIVE_PERFORMANCE`) desde este módulo, para que las dos tiendas puntúen con el mismo criterio y sean comparables entre sí. La tabla cubre desde Maxwell/Polaris (lo habitual en el mercado de segunda mano) hasta la generación actual; los chips sin dato de benchmark fiable se quedan fuera a propósito y salen marcados como `SIN_DATO_BENCHMARK` en vez de inventar un número.

## Requisitos

Solo librería estándar de Python 3.8+ (`json`, `re`, `csv`, `time`, `sys`, `urllib.request`, `math`). No hace falta `pip install` nada.

## Notas importantes

- **Los scripts son de uso personal/puntual**, no para scraping masivo o continuo: incluyen `time.sleep()` entre peticiones y reintentos con backoff para no machacar el servidor. Si vas a tocar el código, mantén ese espaciado.
- Los benchmarks de CPU (`CINEBENCH_R23_MULTI`) son promedios agregados de Notebookcheck en la fecha en que se escribieron — pueden variar ±15% según el chasis/refrigeración concreto del equipo, y no se actualizan solos.
- El scraping de pccomponentes se apoya en que la web incruste un bloque JSON-LD (`microdata-product-list-script`) en el listado y una tabla de specs con `<strong>Campo</strong>` en la ficha de producto. Si la web cambia esa estructura, los regex de extracción dejarán de encontrar coincidencias (los campos saldrán como `None`/`sin dato`) y habrá que actualizar los patrones en `DETAIL_FIELD_PATTERNS`.
- El scraping de CeX depende de dos cosas que no controla este repo: que `v3/appsettings/prelogin` siga exponiendo `algoliaAppId`/`algoliaSearchAppKey`/`algoliaIndexName`, y que `categoryId` 887 siga siendo "Tarjetas Graficas - PCi-E" (se puede comprobar visitando `es.webuy.com/search?categoryIds=887&...`). Si alguna de las dos cambia, el fallo es evidente (0 resultados o error HTTP), no un dato silenciosamente incorrecto.

## Cómo se usó con Claude

Este repo salió de una conversación con Claude donde el flujo fue, en resumen:

1. Pedir un scraper básico (nombre/precio/specs) para un caso de uso concreto.
2. Revisar los resultados con sentido crítico ("¿por qué gana este si tiene peor CPU real?", "¿esto de RAM soldada lo sabemos seguro?") en vez de aceptar el ranking a ciegas.
3. Pedir a Claude que verificara datos dudosos contra fuentes externas (Notebookcheck para benchmarks reales, reviews independientes para térmica/ruido, la propia ficha de producto para RAM ampliable) en vez de inventar números.
4. Iterar el scoring en base a esas correcciones, hasta llegar a algo que reflejara valor real de uso y no solo specs en una tabla.

Si quieres extender esto con Claude Code, algunas peticiones que funcionan bien dado cómo está construido el código:

- *"Cambia el rango de precio a X-Y y vuelve a correr el scraper"* — solo hay que tocar `BASE_URL`.
- *"Añade una categoría nueva (ej. monitores, auriculares) con su propio scoring"* — se puede partir de `scrape_tablets.py` como plantilla, ya que es el más simple de los dos.
- *"Verifica el dato de RAM ampliable del top 5 antes de comprar"* — ya existe `fetch_product_detail()` / `DETAIL_FIELD_PATTERNS`, solo hay que apuntarlo a los campos que falten.
- *"Ajusta los pesos del score para priorizar X sobre Y"* — los pesos están comentados en cada función `score()` con el motivo detrás de cada número, para poder discutirlos en vez de cambiarlos a ciegas.
