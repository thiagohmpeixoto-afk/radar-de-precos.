import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import unescape
from pathlib import Path
from statistics import median, quantiles

STORES = {
    "KaBuM!": "https://www.kabum.com.br/hardware/ssd-2-5/ssd-pcie-nvme",
    "Magazine Luiza": "https://www.magazineluiza.com.br",
    "Casas Bahia": "https://www.casasbahia.com.br",
    "Amazon Brasil": "https://www.amazon.com.br",
    "Mercado Livre": "https://www.mercadolivre.com.br",
    "Ponto": "https://www.pontofrio.com.br",
    "Carrefour": "https://www.carrefour.com.br",
    "Fast Shop": "https://www.fastshop.com.br",
    "Pichau": "https://www.pichau.com.br",
    "TerabyteShop": "https://www.terabyteshop.com.br",
    "Kalunga": "https://www.kalunga.com.br",
    "Lojas Colombo": "https://www.colombo.com.br",
    "Decathlon": "https://www.decathlon.com.br",
    "Leroy Merlin": "https://www.leroymerlin.com.br"
}


def extract_products(page):
    products = []

    scripts = re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.I | re.S
    )

    def visit(item):
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            kind = item.get("@type", "")
            kinds = kind if isinstance(kind, list) else [kind]

            if "Product" in kinds:
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                if isinstance(offers, dict):
                    price = offers.get("price")
                    if price is None:
                        price = offers.get("lowPrice")

                    if price is not None:
                        products.append({
                            "name": str(item.get("name", ""))[:180],
                            "price": str(price),
                            "url": str(offers.get("url", item.get("url", "")))
                        })

            for key in ("@graph", "itemListElement"):
                if key in item:
                    visit(item[key])

            if "item" in item:
                visit(item["item"])

    for script in scripts:
        try:
            visit(json.loads(unescape(script)))
        except (ValueError, TypeError):
            pass

    return products[:100]


def fetch_page(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; RadarDeBugs/2.0)",
            "Accept": "text/html"
        }
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        page = response.read(1500000).decode("utf-8", errors="replace")
        return response.status, page


def kabum_prices(products):
    observations = []
    urls = {
        product["url"]: product["name"]
        for product in products
        if product["name"] and re.fullmatch(
            r"https://www[.]kabum[.]com[.]br/produto/[0-9]+/[a-z0-9-]+",
            product["url"]
        )
    }

    for url, name in list(urls.items())[:3]:
        try:
            _, page = fetch_page(url)
        except OSError as error:
            print("KaBuM! produto indisponível para consulta:", url, error, flush=True)
            continue

        prices = set(re.findall(
            r'R[$]\s*([0-9]+(?:[.][0-9]{3})*,[0-9]{2})</span>\s*'
            r'(?:<span[^>]*>[^<]*(?:<!--.*?-->[^<]*)*</span>\s*)*?'
            r'<span[^>]*>\s*à vista no PIX\s*</span>',
            unescape(page),
            flags=re.I
        ))
        if len(prices) != 1:
            continue

        amount = prices.pop().replace(".", "").replace(",", ".")
        if float(amount) > 0:
            observations.append({
                "name": name,
                "price": amount,
                "currency": "BRL",
                "payment": "PIX",
                "url": url
            })

    return observations


def scan(name, url):
    result = {
        "store": name,
        "url": url,
        "status": "not_validated",
        "products_extracted": 0,
        "http_status": None,
        "error": None
    }

    try:
        result["http_status"], content = fetch_page(url)
        products = extract_products(content)
        if name == "KaBuM!":
            products = kabum_prices(products)
            result["observed_prices"] = products

        result["products_extracted"] = len(products)
        result["status"] = (
            "prices_extracted" if products else "no_structured_prices"
        )

    except urllib.error.HTTPError as error:
        result["http_status"] = error.code
        result["status"] = "blocked_or_http_error"
        result["error"] = str(error)

    except Exception as error:
        result["status"] = "connection_error"
        result["error"] = str(error)[:200]

    return result


def load_previous_report():
    try:
        with open("results.json", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def compare_prices(previous, sources, now):
    today = now.date().isoformat()
    cutoff = (now.date() - timedelta(days=30)).isoformat()
    history = {
        url: {day: price for day, price in daily.items() if cutoff <= day <= today}
        for url, daily in previous.get("kabum_pix_history", {}).items()
    }
    history = {url: daily for url, daily in history.items() if daily}
    offers = []

    # Aproveita a última leitura publicada antes da introdução do histórico.
    previous_day = previous.get("generated_at", "")[:10]
    if cutoff <= previous_day <= today:
        for source in previous.get("sources", []):
            if source["store"] == "KaBuM!":
                for product in source.get("observed_prices", []):
                    history.setdefault(product["url"], {}).setdefault(
                        previous_day, product["price"]
                    )

    for source in sources:
        if source["store"] != "KaBuM!":
            continue

        for product in source.get("observed_prices", []):
            daily = history.setdefault(product["url"], {})
            past = [Decimal(price) for day, price in daily.items() if day < today]
            current = Decimal(product["price"])

            if len(past) >= 7:
                q1, _, q3 = quantiles(past, n=4, method="inclusive")
                lower_fence = q1 - Decimal("1.5") * (q3 - q1)
                if current < lower_fence and current < min(past) - Decimal("1.00"):
                    offers.append({
                        **product,
                        "store": source["store"],
                        "status": "possible_price_error",
                        "historical_median": format(median(past), ".2f"),
                        "historical_low": format(min(past), ".2f"),
                        "reference_days": len(past)
                    })

            daily[today] = product["price"]

    return history, offers


def main():
    previous = load_previous_report()
    sources = []

    for name, url in STORES.items():
        print("Testando:", name, flush=True)
        result = scan(name, url)
        sources.append(result)
        print(result["status"], flush=True)

    now = datetime.now(timezone.utc)
    history, offers = compare_prices(previous, sources, now)
    report = {
        "generated_at": now.isoformat(),
        "minimum_discount": None,
        "maximum_price": None,
        "sources": sources,
        "kabum_pix_history": history,
        "offers": offers
    }

    pending = Path("results.json.tmp")
    with pending.open("w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    pending.replace("results.json")
    print("Diagnostico salvo em results.json")


if __name__ == "__main__":
    main()
