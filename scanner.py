import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape

STORES = {
    "KaBuM!": "https://www.kabum.com.br",
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


def scan(name, url):
    result = {
        "store": name,
        "url": url,
        "status": "not_validated",
        "products_extracted": 0,
        "http_status": None,
        "error": None
    }

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; RadarDeBugs/2.0)",
            "Accept": "text/html"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result["http_status"] = response.status
            content = response.read(1500000).decode(
                "utf-8", errors="replace"
            )

        products = extract_products(content)
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


def main():
    sources = []

    for name, url in STORES.items():
        print("Testando:", name, flush=True)
        result = scan(name, url)
        sources.append(result)
        print(result["status"], flush=True)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "minimum_discount": None,
        "maximum_price": None,
        "sources": sources,
        "offers": []
    }

    with open("results.json", "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print("Diagnostico salvo em results.json")


if __name__ == "__main__":
    main()
