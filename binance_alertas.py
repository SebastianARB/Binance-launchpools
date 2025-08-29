#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Radar de Airdrops / Learn & Earn / Megadrop / Launchpool (Binance)
Descripción: consulta fuentes oficiales de Binance y avisa por Telegram/Discord
"""

import os, json, time, re
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ---------------------- CONFIG ----------------------
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
DISCORD_WEBHOOK  = os.getenv("DISCORD_WEBHOOK", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Python/AlertBot",
    "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# Endpoints CMS
CMS_LIST = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"

# Categorías (Support)
CAT_AIRDROP    = 128  # Crypto Airdrop
CAT_NEWS       = 49   # Latest Binance News
CAT_ACTIVITIES = 93   # Latest Activities

# Fallback HTML
HTML_LIST  = "https://www.binance.com/en/support/announcement/list/{catalog}"
DETAIL_URL = "https://www.binance.com/en/support/announcement/detail/{code}"

# Persistencia
DB_PATH = "visto.json"

# Palabras clave que implican recompensa/dinero
KEYS_MONETIZABLE = [
    # EN
    r"\bairdrop\b", r"\b(reward|rewards)\b", r"\bbonus\b", r"\bvoucher(s)?\b",
    r"\bearn\b", r"\bpayout\b", r"\bdistribute(d)?\b", r"\bprize(s)?\b",
    r"\blaunchpool\b", r"\blaunchpad\b",
    # ES
    r"\brecompensa(s)?\b", r"\bbono(s)?\b", r"\bvale(s)?\b", r"\bpremio(s)?\b",
    r"\bregalo\b", r"\bregalan\b", r"\brepart(o|e)\b", r"\bdistribuci[oó]n\b",
    r"\baprende y gana\b", r"\bgana\b",
    # Símbolos/indicadores
    r"\bUSDT\b", r"\bBUSD\b", r"\bUSD\b", r"\$\d+", r"%\s*APY", r"%\s*APR"
]
_rx_money = re.compile("|".join(KEYS_MONETIZABLE), re.IGNORECASE)

# ---------------------- Utilidades ----------------------
def cargar_vistos():
    if not os.path.exists(DB_PATH):
        return set()
    try:
        return set(json.load(open(DB_PATH, "r", encoding="utf-8")))
    except Exception:
        return set()

def guardar_vistos(ids):
    json.dump(list(ids), open(DB_PATH, "w", encoding="utf-8"))

def normalizar_item(item, categoria):
    code  = item.get("code") or item.get("id") or ""
    title = (item.get("title") or "").strip()
    ts    = item.get("releaseDate") or item.get("createdTime") or None
    if isinstance(ts, (int, float)):
        fecha = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
    else:
        fecha = None
        for key in ("releaseDate", "date", "ctime"):
            if item.get(key):
                try:
                    fecha = dateparser.parse(str(item[key]))
                    break
                except Exception:
                    pass
    link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else ""
    return {"id": code or f"{categoria}:{title}",
            "titulo": title,
            "fecha": fecha.isoformat() if fecha else "",
            "categoria": categoria,
            "link": link}

def fetch_cms(catalog_id, page=1, size=20, keyword=None):
    params = {"type": 1, "catalogId": catalog_id, "pageNo": page, "pageSize": size}
    if keyword: params["keyword"] = keyword
    try:
        r = requests.get(CMS_LIST, params=params, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json().get("data", {})
        rows = data.get("articles") or data.get("catalogs") or data.get("list") or data.get("rows") or []
        return [normalizar_item(it, catalog_id) for it in rows]
    except Exception:
        return []

def fetch_html_list(catalog_id):
    url = HTML_LIST.format(catalog=catalog_id)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for a in soup.select("a[href*='/support/announcement/detail/']"):
            href = a.get("href", "")
            m = re.search(r"/detail/([a-f0-9]+)", href)
            if not m:
                continue
            code  = m.group(1)
            title = a.get_text(strip=True)
            out.append({
                "id": code,
                "titulo": title,
                "fecha": "",
                "categoria": catalog_id,
                "link": "https://www.binance.com" + href if href.startswith("/") else href
            })
        # quitar duplicados preservando orden
        seen, uniq = set(), []
        for it in out:
            if it["id"] in seen: continue
            seen.add(it["id"]); uniq.append(it)
        return uniq
    except Exception:
        return []

def completar_desde_detalle(item):
    """Si falta titulo o link, intenta leerlos desde la página de detalle."""
    if item.get("link") and item.get("titulo"):
        return item
    code = item.get("id") or ""
    if not item.get("link") and code:
        item["link"] = DETAIL_URL.format(code=code)
    if item.get("link") and not item.get("titulo"):
        try:
            r = requests.get(item["link"], headers=HEADERS, timeout=20)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                t = ((soup.select_one("meta[property='og:title']") or {}).get("content")
                     or (soup.select_one("h1") or {}).get_text(strip=True)
                     or (soup.title.get_text(strip=True) if soup.title else ""))
                item["titulo"] = (t or "").strip()
        except Exception:
            pass
    return item

def filtrar_learn_and_earn(items):
    s = ("learn & earn", "aprende y gana", "learn and earn")
    return [x for x in items if any(k in x["titulo"].lower() for k in s)]

def filtrar_megadrop_o_hodler(items):
    s = ("megadrop", "hodler airdrops")
    return [x for x in items if any(k in x["titulo"].lower() for k in s)]

def filtrar_launchpool(items):
    s = ("launchpool", "launch pad", "launchpad")
    return [x for x in items if any(k in x["titulo"].lower() for k in s)]

def es_monetizable(item):
    titulo = (item.get("titulo") or "").strip()
    return bool(_rx_money.search(titulo))

def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "disable_web_page_preview": True}
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception:
        return False

def enviar_discord(texto):
    if not DISCORD_WEBHOOK:
        print("[WARN] Falta DISCORD_WEBHOOK")
        return False
    try:
        r = requests.post(DISCORD_WEBHOOK, json={"content": texto}, timeout=15)
        if 200 <= r.status_code < 300:
            print(f"[DEBUG] Discord OK: {r.status_code}")
            return True
        print(f"[ERR] Discord {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[ERR] Discord exception: {e}")
        return False

print(f"[DEBUG] Webhook presente: {bool(DISCORD_WEBHOOK)}")

def notificar(item):
    titulo = item.get("titulo") or "Anuncio"
    link   = item.get("link")   or "https://www.binance.com/en/support/announcement"
    fecha_str = ""
    if item.get("fecha"):
        try:
            dt = dateparser.parse(item["fecha"])
            fecha_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass
    cat = item.get("categoria")
    etiqueta = "Airdrop" if cat == CAT_AIRDROP else ("Activities/News" if cat in (CAT_ACTIVITIES, CAT_NEWS) else f"Cat {cat}")
    mensaje = f"🟡 {etiqueta} — {titulo}\n{('🗓 '+fecha_str) if fecha_str else ''}\n🔗 {link}"
    ok_tg = enviar_telegram(mensaje)
    ok_dc = enviar_discord(mensaje)
    return ok_tg or ok_dc

def revisar_categoria(catalog_id, filtro=None):
    items = fetch_cms(catalog_id)
    if not items:
        items = fetch_html_list(catalog_id)
    if filtro:
        items = filtro(items)
    return items

def main():
    vistos = cargar_vistos()

    # A) Airdrops oficiales
    airdrops = revisar_categoria(CAT_AIRDROP)

    # B) Learn & Earn (News + Activities)
    lae_news = revisar_categoria(CAT_NEWS, filtro=filtrar_learn_and_earn)
    lae_act  = revisar_categoria(CAT_ACTIVITIES, filtro=filtrar_learn_and_earn)

    # C) Megadrop / Hodler
    mega_news = revisar_categoria(CAT_NEWS, filtro=filtrar_megadrop_o_hodler)

    # D) Launchpool / Launchpad
    launchpool_news = revisar_categoria(CAT_NEWS, filtro=filtrar_launchpool)

    # Reunir candidatos de todas las categorías (evitando repetidos vistos)
    candidatos = []
    for lista in (airdrops, lae_news, lae_act, mega_news, launchpool_news):
        for it in lista:
            if not it.get("id") or it["id"] in vistos:
                continue
            candidatos.append(it)

    # Quedarnos solo con lo que “regala dinero”
    nuevos = [it for it in candidatos if es_monetizable(it)]

    # Completar datos y filtrar vacíos
    enriquecidos = []
    for it in nuevos:
        it = completar_desde_detalle(it)
        if not it.get("titulo") or not it.get("link"):
            print(f"[SKIP] Faltan datos (titulo/link) para id={it.get('id')}")
            continue
        enriquecidos.append(it)

    enviados = 0
    for it in enriquecidos:
        if notificar(it):
            enviados += 1
        vistos.add(it["id"])
        time.sleep(0.5)

    guardar_vistos(vistos)
    print(f"[{datetime.utcnow().isoformat()}] Revisado. Nuevos: {len(nuevos)}, Enviados: {enviados}")

if __name__ == "__main__":
    main()

    # Si el workflow se lanzó manualmente con test=true, enviar mensaje de prueba
    if os.getenv("TEST_MODE", "").lower() == "true":
        notificar({
            "id": "test-manual",
            "titulo": "🧪 Test manual desde GitHub Actions",
            "fecha": datetime.utcnow().isoformat(),
            "categoria": CAT_AIRDROP,
            "link": "https://www.binance.com/en/support/announcement"
        })


