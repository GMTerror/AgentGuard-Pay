"""Catalog sources. The policy gate trusts only what a catalog's .get() returns.

For Channel3 that is a server-side SNAPSHOT of an offer the agent was actually shown
(merchant + price at search time), valid for `ttl` seconds. The agent therefore can't
invent a product id, a price or a merchant, and a stale price can't be bought later.
Product titles and descriptions stay untrusted text: they go to the model and the risk
scorer, never into a decision.
"""

import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests

CHANNEL3_URL = "https://api.trychannel3.com"
MAX_OFFERS_PER_PRODUCT = 3
MAX_NAME, MAX_DESC = 120, 300


class CatalogError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(f"catalog {status}: {message}")
        self.status = status


class StaticCatalog(dict):
    """item_id -> {name, description, merchant, price, currency}. Used by the offline demos."""

    def search(self, query: str) -> list:
        q = query.lower()
        return [
            {"item_id": i, **e}
            for i, e in self.items()
            if q in i.lower()
            or q in e.get("name", "").lower()
            or q in e.get("description", "").lower()
        ][:10]


def _price(offer: dict):
    """Docs show both {"price": n} and {"amount": n}; accept either. None if unusable."""
    p = offer.get("price")
    if not isinstance(p, dict):
        return None, None
    try:
        d = Decimal(str(p.get("price", p.get("amount"))))
        if not d.is_finite() or d <= 0:
            return None, None
        return d.quantize(Decimal("0.01"), ROUND_HALF_UP), str(
            p.get("currency", "")
        ).upper()
    except (InvalidOperation, ValueError):
        return None, None


def _image(prod: dict):
    """Main image, preferring Channel3's background-removed square (made for product grids). https only."""
    imgs = [i for i in prod.get("images") or [] if isinstance(i, dict)]
    imgs.sort(key=lambda i: not i.get("is_main_image"))
    for i in imgs:
        for key in ("cleaned_url", "url"):
            u = i.get(key)
            if isinstance(u, str) and u.startswith("https://"):
                return u
    return None


class Channel3Catalog:
    def __init__(
        self,
        api_key: str,
        currency: str = "USD",
        country: str | None = None,
        ttl: int = 900,
        limit: int = 10,
        session=None,
        clock=time.time,
        max_desc: int = MAX_DESC,
        offers_per_product: int = MAX_OFFERS_PER_PRODUCT,
    ):
        self._key, self._currency, self._country = api_key, currency.upper(), country
        self._ttl, self._limit, self._clock = ttl, limit, clock
        self._max_desc, self._per = max_desc, offers_per_product
        self._http = session or requests.Session()
        self._items: dict = {}

    def search(self, query: str) -> list:
        config = {"currency": self._currency}
        if self._country:
            config["country"] = self._country
        resp = self._http.request(
            "POST",
            CHANNEL3_URL + "/v1/search",
            headers={"x-api-key": self._key},
            json={"query": query, "limit": self._limit, "config": config},
            timeout=15,
        )
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not 200 <= resp.status_code < 300 or not isinstance(data, dict):
            raise CatalogError(resp.status_code, str(data)[:200])

        now = self._clock()
        self._items = {
            k: v for k, v in self._items.items() if now - v["at"] <= self._ttl
        }
        shown = []
        for prod in data.get("products") or []:
            if (
                not isinstance(prod, dict)
                or not isinstance(prod.get("id"), str)
                or not isinstance(prod.get("offers"), list)
            ):
                continue
            name = str(prod.get("title") or "")[:MAX_NAME]
            desc = str(prod.get("description") or "")[: self._max_desc]
            image = _image(prod)
            offers = []
            for o in prod["offers"]:
                if (
                    not isinstance(o, dict)
                    or o.get("availability") != "InStock"
                    or not isinstance(o.get("domain"), str)
                ):
                    continue
                price, cur = _price(o)
                if price is not None and cur == self._currency:
                    offers.append((price, o["domain"].lower().removeprefix("www.")))
            seen = set()
            for price, domain in sorted(offers)[: self._per]:
                item_id = f"{prod['id']}@{domain}"
                if item_id in seen:
                    continue
                seen.add(item_id)
                entry = {
                    "merchant": domain,
                    "price": str(price),
                    "currency": self._currency,
                    "name": name,
                    "description": desc,
                    "image": image,
                    "at": now,
                }
                self._items[item_id] = entry
                shown.append(
                    {
                        "item_id": item_id,
                        "name": name,
                        "description": desc,
                        "merchant": domain,
                        "price": str(price),
                        "currency": self._currency,
                    }
                )
        return shown

    def get(self, item_id: str):
        e = self._items.get(item_id)
        if e is None or self._clock() - e["at"] > self._ttl:
            return None
        return e
