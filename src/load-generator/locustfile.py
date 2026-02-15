#!/usr/bin/python

import copy
import json
import logging
import os
import random
import threading
import uuid
from collections import Counter

import requests
from locust import HttpUser, between, events, task
from locust_plugins.users.playwright import PageWithRetry, PlaywrightUser, pw
from opentelemetry import baggage, context
from playwright.async_api import Request, Route

# --- Logging standard ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.info("Starting load-generator")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("true", "yes", "on", "1")


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float_optional(name):
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    if val < 0:
        return 0.0
    if val > 1:
        return 1.0
    return val


def env_csv(name, default_values):
    raw = os.environ.get(name, "")
    if not raw.strip():
        return list(default_values)
    values = [x.strip() for x in raw.split(",") if x.strip()]
    return values or list(default_values)


DEFAULT_PRODUCTS = [
    {"id": "0PUK6V6EV0", "name": "Solar System Color Imager", "categories": ["accessories", "telescopes"]},
    {"id": "1YMWWN1N4O", "name": "Eclipsmart Travel Refractor Telescope", "categories": ["telescopes", "travel"]},
    {"id": "2ZYFJ3GM2N", "name": "Roof Binoculars", "categories": ["binoculars"]},
    {"id": "66VCHSJNUP", "name": "Starsense Explorer Refractor Telescope", "categories": ["telescopes"]},
    {"id": "6E92ZMYYFZ", "name": "Solar Filter", "categories": ["accessories", "telescopes"]},
    {"id": "9SIQT8TOJO", "name": "Optical Tube Assembly", "categories": ["accessories", "telescopes", "assembly"]},
    {"id": "L9ECAV7KIM", "name": "Lens Cleaning Kit", "categories": ["accessories"]},
    {"id": "LS4PSXUNUM", "name": "Red Flashlight", "categories": ["accessories", "flashlights"]},
    {"id": "OLJCESPC7Z", "name": "National Park Foundation Explorascope", "categories": ["telescopes"]},
    {"id": "HQTGWGPNH4", "name": "The Comet Book", "categories": ["books"]},
]

DEFAULT_CATEGORIES = sorted({cat for p in DEFAULT_PRODUCTS for cat in p["categories"]})
DEFAULT_CURRENCIES = ["USD", "EUR", "CHF", "CAD"]

with open("people.json", encoding="utf-8") as people_file:
    people = json.load(people_file)

WAIT_MIN_SECONDS = env_float("LOCUST_WAIT_MIN_SECONDS", 1.5)
WAIT_MAX_SECONDS = env_float("LOCUST_WAIT_MAX_SECONDS", 6.0)
BROWSER_TRAFFIC_ENABLED = env_bool("LOCUST_BROWSER_TRAFFIC_ENABLED", True)
NEGATIVE_TESTS_ENABLED = env_bool("LOCUST_NEGATIVE_TESTS_ENABLED", False)
NEGATIVE_TEST_RATE = env_float("LOCUST_NEGATIVE_TEST_RATE", 0.03)
AUTH_RATIO = env_float_optional("LOCUST_AUTH_RATIO")
BOOTSTRAP_TIMEOUT_SECONDS = env_float("LOCUST_BOOTSTRAP_TIMEOUT_SECONDS", 5.0)
CHECKOUT_MULTI_ITEM_MAX = max(2, env_int("LOCUST_CHECKOUT_MULTI_ITEM_MAX", 4))
CURRENCY_MIX = env_csv("LOCUST_CURRENCY_MIX", DEFAULT_CURRENCIES)

SCENARIO_WEIGHTS = {
    "anonymous": env_int("LOCUST_WEIGHT_ANONYMOUS_BROWSER", 8),
    "returning": env_int("LOCUST_WEIGHT_RETURNING_BUYER", 4),
    "authenticated": env_int("LOCUST_WEIGHT_AUTHENTICATED_USER", 2),
    "power": env_int("LOCUST_WEIGHT_POWER_USER", 1),
    "browser": env_int("LOCUST_WEIGHT_BROWSER_TRAFFIC", 1),
}


def parse_scenario_mix():
    raw = os.environ.get("LOCUST_SCENARIO_MIX", "").strip()
    if not raw:
        return
    alias = {
        "anonymous": "anonymous",
        "anon": "anonymous",
        "returning": "returning",
        "buyer": "returning",
        "authenticated": "authenticated",
        "auth": "authenticated",
        "power": "power",
        "browser": "browser",
    }
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        key, val = pair.split(":", 1)
        normalized = alias.get(key.strip().lower())
        if not normalized:
            continue
        try:
            SCENARIO_WEIGHTS[normalized] = max(0, int(val.strip()))
        except ValueError:
            continue


def apply_auth_ratio():
    if AUTH_RATIO is None:
        return
    other_total = (
        SCENARIO_WEIGHTS["anonymous"]
        + SCENARIO_WEIGHTS["returning"]
        + SCENARIO_WEIGHTS["power"]
    )
    if AUTH_RATIO == 1:
        SCENARIO_WEIGHTS["authenticated"] = max(1, other_total or 1)
        SCENARIO_WEIGHTS["anonymous"] = 0
        SCENARIO_WEIGHTS["returning"] = 0
        SCENARIO_WEIGHTS["power"] = 0
        return
    if AUTH_RATIO == 0:
        SCENARIO_WEIGHTS["authenticated"] = 0
        return
    target_auth = max(1, int(round(other_total * AUTH_RATIO / (1 - AUTH_RATIO))))
    SCENARIO_WEIGHTS["authenticated"] = target_auth


parse_scenario_mix()
apply_auth_ratio()
if sum(SCENARIO_WEIGHTS.values()) <= 0:
    SCENARIO_WEIGHTS["anonymous"] = 1

logging.info("Load generator config: scenario_weights=%s", SCENARIO_WEIGHTS)
logging.info(
    "Load generator config: negative_tests=%s rate=%.3f auth_ratio=%s currency_mix=%s",
    NEGATIVE_TESTS_ENABLED,
    NEGATIVE_TEST_RATE,
    AUTH_RATIO,
    CURRENCY_MIX,
)


class KPICollector:
    def __init__(self):
        self.lock = threading.Lock()
        self.request_total = Counter()
        self.request_failures = Counter()
        self.flow = Counter()
        self.injected_baggage_requests = 0
        self.total_wrapped_requests = 0

    def reset(self):
        with self.lock:
            self.request_total = Counter()
            self.request_failures = Counter()
            self.flow = Counter()
            self.injected_baggage_requests = 0
            self.total_wrapped_requests = 0

    def record_request(self, name, failed):
        with self.lock:
            self.request_total[name] += 1
            if failed:
                self.request_failures[name] += 1

    def record_flow(self, metric_name):
        with self.lock:
            self.flow[metric_name] += 1

    def record_header_injection(self, has_baggage):
        with self.lock:
            self.total_wrapped_requests += 1
            if has_baggage:
                self.injected_baggage_requests += 1


KPI = KPICollector()

TRACKED_ENDPOINTS = {
    "GET /",
    "GET /api/products",
    "GET /api/products/{id}",
    "GET /api/recommendations",
    "GET /api/data",
    "GET /api/cart",
    "POST /api/cart",
    "DELETE /api/cart",
    "POST /api/checkout",
    "GET /api/currency",
    "GET /api/shipping",
    "POST /api/auth/register",
    "POST /api/auth/login",
    "POST /api/auth/verify",
}

CATALOG_STATE = {
    "bootstrapped": False,
    "products": list(DEFAULT_PRODUCTS),
    "categories": list(DEFAULT_CATEGORIES),
}
CATALOG_LOCK = threading.Lock()


def parse_products_payload(payload):
    if isinstance(payload, dict):
        payload = payload.get("products", [])
    if not isinstance(payload, list):
        return None
    parsed = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        product_id = item.get("id")
        if not product_id:
            continue
        categories = item.get("categories") or []
        if not isinstance(categories, list):
            categories = []
        parsed.append(
            {
                "id": product_id,
                "name": item.get("name", product_id),
                "categories": [c for c in categories if isinstance(c, str) and c],
            }
        )
    return parsed or None


def bootstrap_catalog_if_needed():
    if CATALOG_STATE["bootstrapped"]:
        return
    with CATALOG_LOCK:
        if CATALOG_STATE["bootstrapped"]:
            return
        host = os.environ.get("LOCUST_HOST", "").rstrip("/")
        if not host:
            logging.warning("Catalog bootstrap skipped because LOCUST_HOST is empty. Using fallback catalog.")
            CATALOG_STATE["bootstrapped"] = True
            return
        try:
            response = requests.get(
                f"{host}/api/products",
                params={"currencyCode": "USD"},
                timeout=BOOTSTRAP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            parsed = parse_products_payload(response.json())
            if not parsed:
                raise ValueError("empty_or_invalid_product_payload")
            categories = sorted({cat for p in parsed for cat in p["categories"]})
            CATALOG_STATE["products"] = parsed
            CATALOG_STATE["categories"] = categories or list(DEFAULT_CATEGORIES)
            logging.info(
                "Catalog bootstrap completed: products=%d categories=%d",
                len(CATALOG_STATE["products"]),
                len(CATALOG_STATE["categories"]),
            )
        except Exception as exc:
            logging.warning("Catalog bootstrap failed (%s). Using fallback catalog.", exc)
        finally:
            CATALOG_STATE["bootstrapped"] = True


def build_baggage_value(session_id):
    return f"session.id={session_id},synthetic_request=true"


def build_unique_email(base_email, unique_hint):
    suffix = "".join(ch for ch in str(unique_hint).lower() if ch.isalnum())[-12:]
    if not suffix:
        suffix = uuid.uuid4().hex[:12]
    if not base_email or "@" not in base_email:
        return f"locust+{suffix}@example.com"
    local_part, domain_part = base_email.split("@", 1)
    clean_local = "".join(ch for ch in local_part if ch.isalnum() or ch in "._+-").strip(".")
    if not clean_local:
        clean_local = "locust"
    return f"{clean_local}+{suffix}@{domain_part}"


def pick_person():
    return copy.deepcopy(random.choice(people))


def pick_random_product():
    return random.choice(CATALOG_STATE["products"])


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    KPI.reset()
    bootstrap_catalog_if_needed()
    logging.info("Load test started. Tracking %d core endpoints.", len(TRACKED_ENDPOINTS))


@events.request.add_listener
def on_request(
    request_type=None,
    name=None,
    response_time=None,
    response_length=None,
    response=None,
    context=None,
    exception=None,
    **kwargs,
):
    if not name:
        return
    KPI.record_request(name, failed=exception is not None)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    seen_endpoints = {ep for ep in TRACKED_ENDPOINTS if KPI.request_total.get(ep, 0) > 0}
    coverage = (len(seen_endpoints) / len(TRACKED_ENDPOINTS)) * 100 if TRACKED_ENDPOINTS else 100
    add_ok = KPI.flow.get("add_to_cart.success", 0)
    checkout_ok = KPI.flow.get("checkout.success", 0)
    checkout_conversion = (checkout_ok / add_ok) * 100 if add_ok else 0
    baggage_ratio = (
        (KPI.injected_baggage_requests / KPI.total_wrapped_requests) * 100
        if KPI.total_wrapped_requests
        else 0
    )

    logging.info(
        "[KPI] endpoint_coverage=%.1f%% (%d/%d)",
        coverage,
        len(seen_endpoints),
        len(TRACKED_ENDPOINTS),
    )
    logging.info(
        "[KPI] cart_to_checkout_conversion=%.1f%% (checkout_success=%d, add_to_cart_success=%d)",
        checkout_conversion,
        checkout_ok,
        add_ok,
    )
    logging.info(
        "[KPI] baggage_injection_ratio=%.1f%% (%d/%d)",
        baggage_ratio,
        KPI.injected_baggage_requests,
        KPI.total_wrapped_requests,
    )

    for endpoint in sorted(TRACKED_ENDPOINTS):
        total = KPI.request_total.get(endpoint, 0)
        failures = KPI.request_failures.get(endpoint, 0)
        error_rate = (failures / total) * 100 if total else 0
        logging.info(
            "[KPI] endpoint=%s total=%d failures=%d error_rate=%.1f%%",
            endpoint,
            total,
            failures,
            error_rate,
        )


class BaseWebsiteUser(HttpUser):
    abstract = True
    wait_time = between(WAIT_MIN_SECONDS, WAIT_MAX_SECONDS)
    scenario_name = "base"

    def on_start(self):
        bootstrap_catalog_if_needed()
        self.session_id = str(uuid.uuid4())
        self.user_id = str(uuid.uuid4())
        self.currency_code = random.choice(CURRENCY_MIX)
        self.auth_token = None
        self.username = None
        self.password = None
        self.cart_items = {}
        self._otel_token = None
        ctx = baggage.set_baggage("session.id", self.session_id)
        ctx = baggage.set_baggage("synthetic_request", "true", context=ctx)
        self._otel_token = context.attach(ctx)
        self.index()

    def on_stop(self):
        if self._otel_token is not None:
            context.detach(self._otel_token)
            self._otel_token = None

    def _build_headers(self, extra_headers=None):
        headers = {
            "baggage": build_baggage_value(self.session_id),
            "x-load-generator-scenario": self.scenario_name,
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if extra_headers:
            headers.update(extra_headers)
        KPI.record_header_injection("baggage" in headers)
        return headers

    def _extract_json(self, response):
        if not response.text:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def _request(
        self,
        method,
        path,
        *,
        name,
        expected_statuses=(200,),
        params=None,
        json_body=None,
        headers=None,
    ):
        call = getattr(self.client, method.lower())
        with call(
            path,
            name=name,
            headers=self._build_headers(headers),
            params=params,
            json=json_body,
            catch_response=True,
        ) as response:
            if response.status_code in expected_statuses:
                response.success()
                return True, self._extract_json(response)
            response.failure(f"Unexpected status {response.status_code}; expected {expected_statuses}")
            return False, self._extract_json(response)

    def index(self):
        self._request("GET", "/", name="GET /", expected_statuses=(200,))

    def list_products(self):
        return self._request(
            "GET",
            "/api/products",
            name="GET /api/products",
            expected_statuses=(200,),
            params={"currencyCode": self.currency_code},
        )

    def get_product(self, product_id=None):
        if product_id is None:
            product_id = pick_random_product()["id"]
        return self._request(
            "GET",
            f"/api/products/{product_id}",
            name="GET /api/products/{id}",
            expected_statuses=(200,),
            params={"currencyCode": self.currency_code},
        )

    def get_recommendations(self, product_ids=None):
        if product_ids is None:
            product_ids = [pick_random_product()["id"]]
        return self._request(
            "GET",
            "/api/recommendations",
            name="GET /api/recommendations",
            expected_statuses=(200,),
            params={
                "productIds": product_ids,
                "sessionId": self.user_id,
                "currencyCode": self.currency_code,
            },
        )

    def get_ads(self, context_keys=None):
        if context_keys is None:
            if random.random() < 0.2:
                context_keys = []
            else:
                context_keys = [random.choice(CATALOG_STATE["categories"])]
        return self._request(
            "GET",
            "/api/data",
            name="GET /api/data",
            expected_statuses=(200,),
            params={"contextKeys": context_keys},
        )

    def get_supported_currencies(self):
        ok, payload = self._request(
            "GET",
            "/api/currency",
            name="GET /api/currency",
            expected_statuses=(200,),
        )
        if ok and isinstance(payload, list) and payload:
            self.currency_code = random.choice(payload)
        return ok, payload

    def view_cart(self):
        ok, payload = self._request(
            "GET",
            "/api/cart",
            name="GET /api/cart",
            expected_statuses=(200,),
            params={"sessionId": self.user_id, "currencyCode": self.currency_code},
        )
        if ok and isinstance(payload, dict):
            items = payload.get("items") or []
            self.cart_items = {
                item.get("productId"): int(item.get("quantity", 0))
                for item in items
                if item.get("productId")
            }
        return ok, payload

    def add_to_cart(self, product_id=None, quantity=None):
        product_id = product_id or pick_random_product()["id"]
        quantity = quantity or random.choice([1, 1, 1, 2, 2, 3, 4, 5])
        self.get_product(product_id)
        ok, payload = self._request(
            "POST",
            "/api/cart",
            name="POST /api/cart",
            expected_statuses=(200,),
            params={"currencyCode": self.currency_code},
            json_body={
                "item": {"productId": product_id, "quantity": quantity},
                "userId": self.user_id,
            },
        )
        if ok:
            KPI.record_flow("add_to_cart.success")
            self.cart_items[product_id] = self.cart_items.get(product_id, 0) + quantity
        return ok, payload

    def clear_cart(self):
        ok, payload = self._request(
            "DELETE",
            "/api/cart",
            name="DELETE /api/cart",
            expected_statuses=(200, 204),
            json_body={"userId": self.user_id},
        )
        if ok:
            self.cart_items = {}
        return ok, payload

    def get_shipping_quote(self):
        if not self.cart_items:
            self.add_to_cart()
        cart_item_list = [
            {"productId": product_id, "quantity": quantity}
            for product_id, quantity in self.cart_items.items()
        ]
        address = pick_person()["address"]
        return self._request(
            "GET",
            "/api/shipping",
            name="GET /api/shipping",
            expected_statuses=(200,),
            params={
                "itemList": json.dumps(cart_item_list),
                "currencyCode": self.currency_code,
                "address": json.dumps(address),
            },
        )

    def checkout(self, multi_item=False):
        target_size = random.randint(2, CHECKOUT_MULTI_ITEM_MAX) if multi_item else 1
        while len(self.cart_items) < target_size:
            self.add_to_cart()

        order_profile = pick_person()
        order_profile["userId"] = self.user_id
        order_profile["userCurrency"] = self.currency_code
        KPI.record_flow("checkout.attempt")
        ok, payload = self._request(
            "POST",
            "/api/checkout",
            name="POST /api/checkout",
            expected_statuses=(200,),
            params={"currencyCode": self.currency_code},
            json_body=order_profile,
        )
        if ok:
            KPI.record_flow("checkout.success")
            self.cart_items = {}
        return ok, payload

    def register(self):
        person = pick_person()
        username = f"locust_{uuid.uuid4().hex[:12]}"
        password = os.environ.get("LOCUST_AUTH_PASSWORD", "Passw0rd!Locust")
        body = {
            "username": username,
            "password": password,
            "email": build_unique_email(person.get("email"), username),
            "address": person.get("address", {}).get("streetAddress"),
            "zip": person.get("address", {}).get("zipCode"),
            "city": person.get("address", {}).get("city"),
            "state": person.get("address", {}).get("state"),
            "country": person.get("address", {}).get("country"),
            "phone": "0000000000",
        }
        ok, payload = self._request(
            "POST",
            "/api/auth/register",
            name="POST /api/auth/register",
            expected_statuses=(200, 201),
            json_body=body,
        )
        if ok:
            KPI.record_flow("auth.register.success")
            self.username = username
            self.password = password
        return ok, payload

    def login(self):
        if not self.username or not self.password:
            register_ok, _ = self.register()
            if not register_ok:
                return False, None
        ok, payload = self._request(
            "POST",
            "/api/auth/login",
            name="POST /api/auth/login",
            expected_statuses=(200,),
            json_body={"username": self.username, "password": self.password},
        )
        if ok and isinstance(payload, dict) and payload.get("token"):
            KPI.record_flow("auth.login.success")
            self.auth_token = payload["token"]
        return ok, payload

    def verify_token(self):
        if not self.auth_token:
            login_ok, _ = self.login()
            if not login_ok:
                return False, None
        ok, payload = self._request(
            "POST",
            "/api/auth/verify",
            name="POST /api/auth/verify",
            expected_statuses=(200,),
            json_body={"token": self.auth_token},
        )
        if ok and isinstance(payload, dict):
            if payload.get("valid") is True:
                KPI.record_flow("auth.verify.success")
        return ok, payload

    def maybe_run_negative_tests(self):
        if not NEGATIVE_TESTS_ENABLED:
            return
        if random.random() >= NEGATIVE_TEST_RATE:
            return

        test_type = random.choice(["products_method", "auth_method", "invalid_token"])
        if test_type == "products_method":
            self._request(
                "POST",
                "/api/products",
                name="POST /api/products [negative]",
                expected_statuses=(405,),
                json_body={},
            )
        elif test_type == "auth_method":
            self._request(
                "GET",
                "/api/auth/login",
                name="GET /api/auth/login [negative]",
                expected_statuses=(405,),
            )
        else:
            self._request(
                "POST",
                "/api/auth/verify",
                name="POST /api/auth/verify [negative]",
                expected_statuses=(200,),
                json_body={"token": f"invalid-token-{uuid.uuid4()}"},
            )


class AnonymousBrowserUser(BaseWebsiteUser):
    scenario_name = "anonymous-browser"
    weight = SCENARIO_WEIGHTS["anonymous"]

    @task(5)
    def browse_catalog(self):
        self.list_products()
        product = pick_random_product()
        self.get_product(product["id"])
        self.get_recommendations([product["id"]])
        self.get_ads(product["categories"][:1])
        self.maybe_run_negative_tests()

    @task(2)
    def home_and_currency(self):
        self.index()
        self.get_supported_currencies()
        self.maybe_run_negative_tests()

    @task(2)
    def view_cart_snapshot(self):
        self.view_cart()
        self.maybe_run_negative_tests()

    @task(1)
    def add_one_item(self):
        self.add_to_cart()
        self.view_cart()
        self.maybe_run_negative_tests()


class ReturningBuyerUser(BaseWebsiteUser):
    scenario_name = "returning-buyer"
    weight = SCENARIO_WEIGHTS["returning"]

    @task(4)
    def browse_then_add(self):
        self.list_products()
        items_to_add = random.randint(1, 2)
        for _ in range(items_to_add):
            product = pick_random_product()
            self.add_to_cart(product_id=product["id"])
            self.get_recommendations([product["id"]])
            self.get_ads(product["categories"][:1])
        self.view_cart()
        self.maybe_run_negative_tests()

    @task(3)
    def shipping_estimate(self):
        self.view_cart()
        self.get_shipping_quote()
        self.maybe_run_negative_tests()

    @task(2)
    def checkout_small(self):
        self.checkout(multi_item=False)
        self.maybe_run_negative_tests()

    @task(1)
    def cart_cleanup(self):
        self.clear_cart()
        self.maybe_run_negative_tests()


class AuthenticatedUser(BaseWebsiteUser):
    scenario_name = "authenticated-user"
    weight = SCENARIO_WEIGHTS["authenticated"]

    def on_start(self):
        super().on_start()
        self.login()
        self.verify_token()

    @task(3)
    def auth_health(self):
        self.verify_token()
        self.maybe_run_negative_tests()

    @task(3)
    def authenticated_browse(self):
        self.list_products()
        product = pick_random_product()
        self.get_product(product["id"])
        self.get_ads(product["categories"][:1])
        self.get_recommendations([product["id"]])
        self.maybe_run_negative_tests()

    @task(3)
    def authenticated_checkout(self):
        self.add_to_cart()
        self.view_cart()
        self.get_shipping_quote()
        self.checkout(multi_item=True)
        self.maybe_run_negative_tests()

    @task(1)
    def relogin(self):
        self.login()
        self.maybe_run_negative_tests()


class PowerUser(BaseWebsiteUser):
    scenario_name = "power-user"
    weight = SCENARIO_WEIGHTS["power"]

    def on_start(self):
        super().on_start()
        if AUTH_RATIO is not None and AUTH_RATIO > 0:
            self.login()
            self.verify_token()

    @task(6)
    def full_funnel(self):
        self.index()
        self.get_supported_currencies()
        self.list_products()
        for _ in range(random.randint(2, 4)):
            product = pick_random_product()
            self.get_product(product["id"])
            self.add_to_cart(product_id=product["id"], quantity=random.randint(1, 3))
            self.get_ads(product["categories"][:1])
            self.get_recommendations([product["id"]])
        self.view_cart()
        self.get_shipping_quote()
        self.checkout(multi_item=True)
        self.clear_cart()
        self.maybe_run_negative_tests()

    @task(2)
    def recommendation_storm(self):
        product_ids = [pick_random_product()["id"] for _ in range(3)]
        self.get_recommendations(product_ids)
        self.get_ads([random.choice(CATALOG_STATE["categories"])])
        self.maybe_run_negative_tests()

    @task(2)
    def auth_cycle(self):
        self.login()
        self.verify_token()
        self.maybe_run_negative_tests()


if BROWSER_TRAFFIC_ENABLED:
    class WebsiteBrowserUser(PlaywrightUser):
        headless = True
        weight = SCENARIO_WEIGHTS["browser"]
        wait_time = between(WAIT_MIN_SECONDS, WAIT_MAX_SECONDS)

        def _ensure_browser_context(self):
            if not getattr(self, "session_id", None):
                self.session_id = str(uuid.uuid4())
            if not getattr(self, "currency_code", None):
                self.currency_code = random.choice(CURRENCY_MIX)

        def on_start(self):
            bootstrap_catalog_if_needed()
            self._ensure_browser_context()

        async def _add_baggage_header(self, route: Route, request: Request):
            self._ensure_browser_context()
            existing_baggage = request.headers.get("baggage", "")
            baggage_value = build_baggage_value(self.session_id)
            headers = {
                **request.headers,
                "baggage": ", ".join(filter(None, (existing_baggage, baggage_value))),
                "x-load-generator-scenario": "browser-traffic",
            }
            await route.continue_(headers=headers)

        @task(2)
        @pw
        async def open_cart_page_and_change_currency(self, page: PageWithRetry):
            await page.route("**/*", self._add_baggage_header)
            try:
                await page.goto("/cart", wait_until="domcontentloaded")
                currency_selector = page.locator('[name="currency_code"]')
                if await currency_selector.count() > 0:
                    await currency_selector.select_option(self.currency_code)
                await page.wait_for_timeout(1500)
            finally:
                await page.unroute("**/*", self._add_baggage_header)

        @task(3)
        @pw
        async def add_product_to_cart(self, page: PageWithRetry):
            await page.route("**/*", self._add_baggage_header)
            try:
                await page.goto("/", wait_until="domcontentloaded")
                await page.click('[data-cy="product-card"]')
                await page.wait_for_selector('[data-cy="product-add-to-cart"]', timeout=10000)
                await page.click('[data-cy="product-add-to-cart"]')
                await page.wait_for_url("**/cart", timeout=10000)
                await page.wait_for_timeout(1200)
            finally:
                await page.unroute("**/*", self._add_baggage_header)
