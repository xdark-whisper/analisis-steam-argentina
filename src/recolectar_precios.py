"""
Recolecta precios de Steam en Argentina y EE. UU. para una muestra de juegos.

Hace dos cosas, en orden:
1. Baja los ~1.000 juegos mas vendidos de la tienda (precio de EE. UU.), los agrupa
   por rango de precio y elige al azar N de cada rango.
2. Para cada juego consulta el precio de lista y el precio final en Argentina y
   en EE. UU., y guarda todo en un CSV con la fecha.

Uso:
    python src/recolectar_precios.py
    python src/recolectar_precios.py --por-rango 15 --paginas 12

Solo usa la biblioteca estandar de Python 3.8+. Usa endpoints de la tienda de Steam
que no estan documentados y pueden cambiar. Tarda ~5 minutos.
"""
import argparse
import csv
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BUSQUEDA = ("https://store.steampowered.com/search/results/?query&start={s}&count=100"
            "&sort_by=_ASC&category1=998&filter=topsellers&cc=us&l=english&infinite=1")
DETALLE = "https://store.steampowered.com/api/appdetails?appids={id}&cc={cc}&l=spanish&filters={f}"

# (nombre, minimo USD, maximo USD exclusivo)
RANGOS = [
    ("0.99-4.99", 0.01, 5.0), ("5-9.99", 5.0, 10.0), ("10-14.99", 10.0, 15.0),
    ("15-19.99", 15.0, 20.0), ("20-29.99", 20.0, 30.0), ("30-39.99", 30.0, 40.0),
    ("40-49.99", 40.0, 50.0), ("50-59.99", 50.0, 60.0), ("60+", 60.0, 10000.0),
]

# Matriz de Valve para Argentina (nov-2023): precio base en USD -> precio sugerido en USD.
# Fuente: https://steamdb.info/blog/steam-turkey-argentina-usd/
MATRIZ_2023 = {
    0.99: 0.89, 1.99: 1.49, 2.99: 1.99, 3.99: 2.49, 4.99: 2.99, 5.99: 3.59,
    6.99: 3.99, 7.99: 4.49, 8.99: 4.99, 9.99: 5.79, 10.99: 6.29, 11.99: 6.59,
    12.99: 7.29, 13.99: 7.79, 14.99: 7.99, 15.99: 8.49, 16.99: 8.99, 17.99: 9.29,
    18.99: 9.89, 19.99: 10.49, 24.99: 12.49, 29.99: 14.99, 34.99: 17.99,
    39.99: 18.99, 44.99: 20.99, 49.99: 22.99, 54.99: 25.99, 59.99: 26.99,
    64.99: 29.99, 69.99: 32.99, 74.99: 34.99, 79.99: 36.99, 84.99: 38.99,
    89.99: 41.99, 99.99: 45.99, 119.99: 55.99, 129.99: 59.99, 149.99: 69.99,
    199.99: 92.99,
}

CAMPOS = ["fecha_utc", "appid", "nombre", "us_inicial_usd", "ar_inicial", "ar_final",
          "descuento_ar_pct", "matriz2023_esperado"]


def get_json(url, reintentos=4):
    espera = 10
    for _ in range(reintentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (analisis-precios)"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 403):  # demasiados pedidos: esperar y reintentar
                time.sleep(espera)
                espera *= 2
                continue
            return None
        except Exception:
            return None
    return None


def a_usd(texto):
    m = re.search(r"\$\s*([0-9]+(?:[.,][0-9]{2})?)", texto or "")
    return float(m.group(1).replace(",", ".")) if m else None


def leer_resultados(html):
    """Devuelve [(appid, nombre, precio_de_lista_usd)] de una pagina de resultados."""
    out = []
    for bloque in re.findall(r"<a [^>]*store\.steampowered\.com/app/\d+.*?</a>", html, re.S):
        mid = re.search(r"/app/(\d+)", bloque)
        mname = re.search(r'<span class="title">(.*?)</span>', bloque, re.S)
        if not (mid and mname):
            continue
        original = re.search(r'discount_original_price">(.*?)</div>', bloque, re.S)
        final = re.search(r'discount_final_price[^>]*>(.*?)</div>', bloque, re.S)
        precio = a_usd(original.group(1)) if original else None
        if precio is None and final:
            precio = a_usd(final.group(1))
        if precio:
            out.append((int(mid.group(1)), re.sub(r"<[^>]+>", "", mname.group(1)).strip(), precio))
    return out


def armar_lista(paginas, por_rango, semilla):
    vistos, juegos = set(), []
    for p in range(paginas):
        datos = get_json(BUSQUEDA.format(s=p * 100))
        if not datos:
            print(f"pagina {p + 1}: sin respuesta")
            continue
        for appid, nombre, precio in leer_resultados(datos.get("results_html", "")):
            if appid not in vistos:
                vistos.add(appid)
                juegos.append((appid, nombre, precio))
        print(f"pagina {p + 1}/{paginas}: {len(juegos)} juegos con precio")
        time.sleep(2)
    azar = random.Random(semilla)  # misma semilla, misma lista
    elegidos = []
    for nombre, lo, hi in RANGOS:
        candidatos = [j for j in juegos if lo <= j[2] < hi]
        azar.shuffle(candidatos)
        elegidos += candidatos[:por_rango]
        print(f"rango {nombre:>10}: {len(candidatos)} candidatos")
    return elegidos


def precio(appid, cc, filtros):
    datos = get_json(DETALLE.format(id=appid, cc=cc, f=filtros))
    time.sleep(1.6)  # Steam limita ~200 pedidos cada 5 minutos
    return (datos or {}).get(str(appid), {})


def consultar(appid, nombre_busqueda):
    us = precio(appid, "us", "basic,price_overview")
    ar = precio(appid, "ar", "price_overview")
    pu = us.get("data", {}).get("price_overview") if us.get("success") else None
    pa = ar.get("data", {}).get("price_overview") if ar.get("success") else None
    if not (pu and pa):
        return None
    base_us = pu["initial"] / 100
    esperado = MATRIZ_2023.get(round(base_us, 2), "")
    return {
        "fecha_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "appid": appid,
        "nombre": us.get("data", {}).get("name", nombre_busqueda),
        "us_inicial_usd": f"{base_us:.2f}",
        "ar_inicial": f"{pa['initial'] / 100:.2f}",
        "ar_final": f"{pa['final'] / 100:.2f}",
        "descuento_ar_pct": pa.get("discount_percent", 0),
        "matriz2023_esperado": esperado,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--por-rango", type=int, default=10)
    ap.add_argument("--paginas", type=int, default=10, help="paginas de 100 resultados")
    ap.add_argument("--semilla", type=int, default=42)
    a = ap.parse_args()

    lista = armar_lista(a.paginas, a.por_rango, a.semilla)
    if not lista:
        sys.exit("No se pudo leer ningun juego de la tienda.")
    salida = f"data/precios_steam_{datetime.now().strftime('%Y-%m-%d')}.csv"
    with open(salida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for i, (appid, nombre, _) in enumerate(lista, 1):
            fila = consultar(appid, nombre)
            if fila:
                w.writerow(fila)
                f.flush()
            print(f"[{i}/{len(lista)}] {nombre}" + ("" if fila else "  (sin precio, se omite)"))
    print("Listo:", salida)


if __name__ == "__main__":
    main()
