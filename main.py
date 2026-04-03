import requests
import time
import re
import logging
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────────────
CSFLOAT_API_KEY = "GEte73Ed62GCbeEsjG9gxgm4rDR5mWkD"  # From csfloat.com/profile → Developer tab
WEBHOOK_URL = "https://discord.com/api/webhooks/1489388345329979402/IEpws8AYOif6H-0oOxOgbggMMgOxzbxmodB3InJfkpex3jPrYBXegNdXVZ5cIp9QhpWe"
HEADERS         = {"Authorization": CSFLOAT_API_KEY}

CHECK_INTERVAL      = 60    # secondes entre chaque cycle complet
PAGE_SIZE           = 50    # ordres par page
ALERT_TTL_HOURS     = 24    # on ré-alerte si toujours outbid après X heures
MAX_RETRIES         = 4     # tentatives max par requête API
RETRY_BASE_DELAY    = 2     # secondes (doublé à chaque retry)
FLOAT_TOLERANCE = 0.0075

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ]
)
log = logging.getLogger()


# ── API avec retry + rate-limit ───────────────────────────────────────────────
def api_get(url: str, params: dict = None) -> dict | list:
    delay = RETRY_BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)

            if r.status_code == 401:
                raise RuntimeError("❌ Clé API rejetée (401) — vérifie CSFLOAT_API_KEY.")

            if r.status_code == 403:
                log.warning(f"  [403] Accès refusé pour {url} params={params} — skip.")
                return {}   # ← on retourne vide, pas de crash

            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 30))
                log.warning(f"  [429] Rate limit — attente {wait}s...")
                time.sleep(wait)
                continue

            if r.status_code in (502, 503, 504):
                log.warning(f"  [{r.status_code}] Serveur indisponible, retry {attempt}/{MAX_RETRIES}...")
                time.sleep(delay)
                delay *= 2
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.ConnectionError:
            log.warning(f"  [RÉSEAU] Connexion perdue, retry {attempt}/{MAX_RETRIES} dans {delay}s...")
            time.sleep(delay)
            delay *= 2
        except requests.exceptions.Timeout:
            log.warning(f"  [TIMEOUT] Retry {attempt}/{MAX_RETRIES}...")
            time.sleep(delay)
            delay *= 2

    log.error(f"  [ÉCHEC] {url} — abandon après {MAX_RETRIES} tentatives.")
    return {}


# ── Parseurs expression ───────────────────────────────────────────────────────
def parse_float_range(expression: str) -> tuple[float, float]:
    low  = re.search(r'FloatValue\s*>=?\s*([0-9.]+)', expression)
    high = re.search(r'FloatValue\s*<=?\s*([0-9.]+)', expression)
    return (
        float(low.group(1))  if low  else 0.0,
        float(high.group(1)) if high else 1.0,
    )


def expression_covers_item(expression: str, def_index: str, paint_index: str) -> bool:
    """Gère les expressions simples ET les groupes OR multi-items."""
    pairs = re.findall(r'DefIndex\s*==\s*(\d+)\s*and\s*PaintIndex\s*==\s*(\d+)', expression)
    pairs += [(b, a) for a, b in re.findall(
        r'PaintIndex\s*==\s*(\d+)\s*and\s*DefIndex\s*==\s*(\d+)', expression
    )]
    return any(d == def_index and p == paint_index for d, p in pairs)


def is_real_competitor(my_min: float, my_max: float,
                        c_min: float,  c_max: float) -> bool:
    """
    Un concurrent est réel si :
      1. Sa range chevauche la mienne (condition de base)
      2. Son max_float est suffisamment proche du mien (à FLOAT_TOLERANCE près)
         → il cible les mêmes items "haut de gamme" de ma range

    Exemples avec FLOAT_TOLERANCE = 0.005 et ma range 0.07 → 0.10294 :
      concurrent 0.00 → 0.0875  : 0.0875 < 0.09794  → PAS concurrent ✅
      concurrent 0.07 → 0.12    : 0.12   >= 0.09794  → concurrent    ✅
      concurrent 0.00 → 0.10    : 0.10   >= 0.09794  → concurrent    ✅
      concurrent 0.00 → 0.09    : 0.09   < 0.09794   → PAS concurrent ✅
    """
    # 1. Vérification du chevauchement de base
    basic_overlap = not (c_max <= my_min or c_min >= my_max)
    if not basic_overlap:
        return False

    # 2. Le max du concurrent doit couvrir presque toute ma range vers le haut
    covers_upper_bound = c_max >= (my_max - FLOAT_TOLERANCE)
    return covers_upper_bound


# ── Fetch mes ordres ──────────────────────────────────────────────────────────
def fetch_my_orders() -> list[dict]:
    all_orders, page = [], 0
    while True:
        data  = api_get(
            "https://csfloat.com/api/v1/me/buy-orders",
            params={"page": page, "limit": PAGE_SIZE, "order": "desc"}
        )
        batch = data.get("orders", [])
        total = data.get("count", 0)
        all_orders.extend(batch)
        if len(all_orders) >= total or not batch:
            break
        page += 1
        time.sleep(0.3)
    return all_orders


# Table de fallback : on mappe (def_index, paint_index) → market_hash_name
# Construite automatiquement depuis tes propres ordres au démarrage
item_name_cache: dict[tuple, str] = {}

def build_name_cache(my_orders: list[dict]):
    """
    Extrait une map (def_index, paint_index) → nom lisible
    depuis l'expression de tes propres ordres.
    CSFloat met 'Item == "Nom du skin"' dans certaines expressions,
    sinon on garde un fallback générique.
    """
    for order in my_orders:
        expr = order.get("expression", "")
        def_m   = re.search(r'DefIndex\s*==\s*(\d+)',   expr)
        paint_m = re.search(r'PaintIndex\s*==\s*(\d+)', expr)
        if not def_m or not paint_m:
            continue
        key = (def_m.group(1), paint_m.group(1))
        if key not in item_name_cache:
            name_m = re.search(r'Item\s*==\s*"([^"]+)"', expr)
            item_name_cache[key] = name_m.group(1) if name_m else f"DefIdx={key[0]} PaintIdx={key[1]}"

# ── Fetch listing (pour nom + ID) ─────────────────────────────────────────────
def get_listing_info(def_index: str, paint_index: str) -> dict | None:
    """
    Essaie d'abord avec def_index+paint_index.
    Si 403/vide, essaie avec market_hash_name (depuis item_name_cache).
    Si toujours rien, retourne None.
    """
    cache_key = (def_index, paint_index)

    # Tentative 1 : filtrage par def_index + paint_index
    data = api_get(
        "https://csfloat.com/api/v1/listings",
        params={
            "def_index":   def_index,
            "paint_index": paint_index,
            "type":        "buy_now",
            "limit":       1,
        }
    )
    listings = data if isinstance(data, list) else data.get("data", [])

    # Tentative 2 : fallback sur market_hash_name si on en a un
    if not listings and cache_key in item_name_cache:
        fallback_name = item_name_cache[cache_key]
        log.info(f"  [fallback] Recherche par nom : {fallback_name}")
        data = api_get(
            "https://csfloat.com/api/v1/listings",
            params={
                "market_hash_name": fallback_name,
                "type":             "buy_now",
                "limit":            1,
            }
        )
        listings = data if isinstance(data, list) else data.get("data", [])

    if not listings:
        return None

    listing  = listings[0]
    raw_name = listing.get("item", {}).get("market_hash_name", item_name_cache.get(cache_key, f"DefIdx={def_index}"))
    return {
        "id":   listing["id"],
        "name": clean_skin_name(raw_name),
    }


# ── Fetch ordres concurrents ──────────────────────────────────────────────────
def fetch_competitor_orders(listing_id: str) -> list[dict]:
    data = api_get(
        f"https://csfloat.com/api/v1/listings/{listing_id}/buy-orders",
        params={"limit": 50}
    )
    if not data:
        return []
    return data if isinstance(data, list) else data.get("orders", [])


# ── Détection du vrai outbid ──────────────────────────────────────────────────
def find_outbidder(competitor_orders, my_price, my_min, my_max, def_index, paint_index) -> dict | None:
    for order in competitor_orders:
        price = order.get("price", 0)
        if price <= my_price:
            break

        expr = order.get("expression", "")
        if not expression_covers_item(expr, def_index, paint_index):
            continue

        c_min, c_max = parse_float_range(expr)

        if is_real_competitor(my_min, my_max, c_min, c_max):  # ← ici
            return order

    return None


# ── Discord ───────────────────────────────────────────────────────────────────
def send_alert(skin_name: str, listing_id: str,
               my_order: dict, competitor: dict,
               c_min: float, c_max: float):
    my_price = my_order["price"]
    c_price  = competitor["price"]
    my_min, my_max = parse_float_range(my_order.get("expression", ""))

    # URL directe vers le listing sur CSFloat
    item_url = f"https://csfloat.com/item/{listing_id}"

    msg = {
        "content": (
            f"🚨 **OUTBID** — **{skin_name}**\n"
            f"💰 Concurrent : **${c_price/100:.2f}** › ton prix : **${my_price/100:.2f}**\n"
            f"📊 Float concurrent : `{c_min:.5f} → {c_max:.5f}`\n"
            f"📊 Ton float :        `{my_min:.5f} → {my_max:.5f}`\n"
            f"🔗 {item_url}"
        )
    }
    try:
        requests.post(WEBHOOK_URL, json=msg, timeout=5)
        log.info(f"    📨 Alerte Discord envoyée.")
    except Exception as e:
        log.warning(f"    [WARN] Discord failed: {e}")


# ── Gestion des alertes avec TTL ──────────────────────────────────────────────
# Structure : { alert_key: datetime_of_first_alert }
alerted: dict[str, datetime] = {}

def should_alert(alert_key: str) -> bool:
    """
    Envoie l'alerte si :
    - jamais alerté pour cette clé, OU
    - alerté il y a plus de ALERT_TTL_HOURS heures (toujours outbid)
    """
    if alert_key not in alerted:
        return True
    age = datetime.now() - alerted[alert_key]
    return age > timedelta(hours=ALERT_TTL_HOURS)

def cleanup_alerted():
    """Supprime les entrées plus vieilles que 2× le TTL pour éviter la fuite mémoire."""
    cutoff = datetime.now() - timedelta(hours=ALERT_TTL_HOURS * 2)
    stale  = [k for k, t in alerted.items() if t < cutoff]
    for k in stale:
        del alerted[k]
    if stale:
        log.info(f"  [cleanup] {len(stale)} alertes expirées supprimées.")


# ── Boucle principale ─────────────────────────────────────────────────────────
listing_cache: dict[tuple, dict] = {}  # (def_index, paint_index) → { id, name }
cycle = 0

log.info("🤖 Bot CSFloat démarré. Surveillance h24...\n")

while True:
    try:
        cycle += 1
        log.info(f"── Cycle #{cycle} ──────────────────────────────────")

        # Nettoyage mémoire toutes les 100 cycles (~1h40 avec 60s d'intervalle)
        if cycle % 100 == 0:
            cleanup_alerted()

        # Rafraîchir le cache listing toutes les 10 cycles (~10 min)
        if cycle % 10 == 0:
            listing_cache.clear()
            log.info("  [cache] Cache listings vidé.")

        # 1. Mes ordres actifs
        my_orders = fetch_my_orders()
        log.info(f"  {len(my_orders)} ordres actifs trouvés.\n")

        build_name_cache(my_orders)

        for my_order in my_orders:
            oid      = my_order["id"]
            expr     = my_order.get("expression", "")
            my_price = my_order["price"]
            my_min, my_max = parse_float_range(expr)

            def_m   = re.search(r'DefIndex\s*==\s*(\d+)',   expr)
            paint_m = re.search(r'PaintIndex\s*==\s*(\d+)', expr)
            if not def_m or not paint_m:
                log.warning(f"  [SKIP] Expression illisible: {expr[:60]}")
                continue

            def_index   = def_m.group(1)
            paint_index = paint_m.group(1)
            cache_key   = (def_index, paint_index)

            # 2. Infos du listing (nom + ID)
            if cache_key not in listing_cache:
                info = get_listing_info(def_index, paint_index)
                if not info:
                    log.info(f"  [--] Aucun listing actif pour DefIdx={def_index} PaintIdx={paint_index}")
                    time.sleep(0.4)
                    continue
                listing_cache[cache_key] = info

            listing_info = listing_cache[cache_key]
            skin_name    = listing_info["name"]
            listing_id   = listing_info["id"]

            log.info(f"  {skin_name} | ${my_price/100:.2f} | float {my_min:.4f}→{my_max:.4f}")

            # 3. Ordres concurrents
            competitors = fetch_competitor_orders(listing_id)
            if not competitors:
                log.info(f"    → Aucun ordre concurrent.")
                time.sleep(0.4)
                continue

            # 4. Détection outbid
            outbidder = find_outbidder(
                competitors, my_price, my_min, my_max, def_index, paint_index
            )

            if outbidder:
                c_price = outbidder["price"]
                c_expr  = outbidder.get("expression", "")
                c_min, c_max = parse_float_range(c_expr)
                alert_key = f"{oid}_{c_price}_{c_min:.5f}_{c_max:.5f}"

                if should_alert(alert_key):
                    send_alert(skin_name, listing_id, my_order, outbidder, c_min, c_max)
                    alerted[alert_key] = datetime.now()
                    log.info(f"    🚨 OUTBID par ${c_price/100:.2f} — alerte envoyée!")
                else:
                    log.info(f"    ⚠️  Toujours outbid à ${c_price/100:.2f} (alerte déjà envoyée)")
            else:
                log.info(f"    ✅ Pas d'outbid réel.")

            time.sleep(0.4)

        log.info(f"\n[✅] Cycle #{cycle} terminé. Prochain dans {CHECK_INTERVAL}s...\n")
        time.sleep(CHECK_INTERVAL)

    except RuntimeError as e:
        # Erreur fatale (clé API invalide) — on arrête
        log.error(e)
        break
    except KeyboardInterrupt:
        log.info("\n[STOP] Arrêt manuel.")
        break
    except Exception as e:
        log.error(f"[ERREUR INATTENDUE] {e}", exc_info=True)
        time.sleep(60)
