import requests
import time

WEBHOOK_URL = "https://discord.com/api/webhooks/1489388345329979402/IEpws8AYOif6H-0oOxOgbggMMgOxzbxmodB3InJfkpex3jPrYBXegNdXVZ5cIp9QhpWe"

orders_config = [
    {"name": "Dual Berettas | Dualing Dragons", "price": 1.15, "min_float": 0.07, "max_float": 0.0777},
    {"name": "XM1014 | Scumbria", "price": 1.10, "min_float": 0.07, "max_float": 0.0777},
    {"name": "R8 Revolver | Crimson Web", "price": 1.30, "min_float": 0.07, "max_float": 0.1118},
    {"name": "MAC-10 | Whitefish", "price": 0.64, "min_float": 0.07, "max_float": 0.0777},
    {"name": "UMP-45 | Corporal", "price": 1.13, "min_float": 0.07, "max_float": 0.08888},
    {"name": "Tec-9 | Snek-9", "price": 0.97, "min_float": 0.07, "max_float": 0.08749},
    {"name": "P90 | Traction", "price": 0.76, "min_float": 0.07, "max_float": 0.08749},
    {"name": "MAC-10 | Carnivore", "price": 0.67, "min_float": 0.07, "max_float": 0.106},
    {"name": "PP-Bizon | Harvester", "price": 0.66, "min_float": 0.07, "max_float": 0.106},
    {"name": "SCAR-20 | Blueprint", "price": 0.56, "min_float": 0.07, "max_float": 0.095},
    {"name": "MAC-10 | Classic Crate", "price": 0.42, "min_float": 0.07, "max_float": 0.10294},
    {"name": "Sawed-Off | Snake Camo", "price": 0.37, "min_float": 0.07, "max_float": 0.1118},
    {"name": "Five-SeveN | Orange Peel", "price": 0.30, "min_float": 0.07, "max_float": 0.12},
    {"name": "MAC-10 | Palm", "price": 0.37, "min_float": 0.07, "max_float": 0.1118},
    {"name": "P250 | Iron Clad", "price": 0.72, "min_float": 0.07, "max_float": 0.103},
    {"name": "USP-S | 27", "price": 1.27, "min_float": 0.07, "max_float": 0.0826},
    {"name": "P2000 | Sure Grip", "price": 0.30, "min_float": 0.07, "max_float": 0.08},
    {"name": "SSG 08 | Memorial", "price": 0.32, "min_float": 0.07, "max_float": 0.09},
    {"name": "XM1014 | Mockingbird", "price": 0.33, "min_float": 0.0, "max_float": 0.09},
    {"name": "MP9 | Nexus", "price": 0.33, "min_float": 0.0, "max_float": 0.09},
    {"name": "MAG-7 | Monster Call", "price": 1.64, "min_float": 0.0, "max_float": 0.093},
]

def get_buy_orders(market_hash_name):
    url = f"https://csfloat.com/api/v1/listings?market_hash_name={market_hash_name}&type=buy_order"
    r = requests.get(url)
    return r.json().get("data", [])

def overlaps(my_min, my_max, other_min, other_max):
    return not (other_max < my_min or other_min > my_max)

def is_real_outbid(order, my_order):
    price = order["price"]

    other_min = order.get("min_float", 0.0)
    other_max = order.get("max_float", 1.0)

    if price <= my_order["price"]:
        return False

    if overlaps(my_order["min_float"], my_order["max_float"], other_min, other_max):
        return True

    return False

def send_alert(name, price, float_range):
    msg = {
        "content": f"🚨 OUTBID: {name}\nPrix concurrent: {price}$\nFloat: {float_range}"
    }
    requests.post(WEBHOOK_URL, json=msg)

already_alerted = set()

while True:
    try:
        for my_order in orders_config:
            orders = get_buy_orders(my_order["name"])

            for order in orders:
                if is_real_outbid(order, my_order):
                    key = f"{my_order['name']}_{order['price']}"

                    if key not in already_alerted:
                        send_alert(
                            my_order["name"],
                            order["price"],
                            f"{order.get('min_float', 0)} - {order.get('max_float', 1)}"
                        )
                        already_alerted.add(key)

                    break

        time.sleep(30)

    except Exception as e:
        print("Erreur:", e)
        time.sleep(60)
