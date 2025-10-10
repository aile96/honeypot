#!/usr/bin/python

import json
import os
import random
import uuid
import logging

from locust import HttpUser, task, between
from locust_plugins.users.playwright import PlaywrightUser, pw, PageWithRetry
from opentelemetry import context, baggage
from playwright.async_api import Route, Request

# --- Logging standard ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.info("Starting load-generator")
logging.info("Instrumentation complete")

categories = [
    "binoculars",
    "telescopes",
    "accessories",
    "assembly",
    "travel",
    "books",
    None,
]

products = [
    "0PUK6V6EV0",
    "1YMWWN1N4O",
    "2ZYFJ3GM2N",
    "66VCHSJNUP",
    "6E92ZMYYFZ",
    "9SIQT8TOJO",
    "L9ECAV7KIM",
    "LS4PSXUNUM",
    "OLJCESPC7Z",
    "HQTGWGPNH4",
]

people_file = open('people.json')
people = json.load(people_file)

class WebsiteUser(HttpUser):
    wait_time = between(5, 10)

    @task(1)
    def index(self):
        self.client.get("/")

    @task(10)
    def browse_product(self):
        self.client.get("/api/products/" + random.choice(products))

    @task(3)
    def get_recommendations(self):
        params = {
            "productIds": [random.choice(products)],
        }
        self.client.get("/api/recommendations", params=params)

    @task(3)
    def get_ads(self):
        params = {
            "contextKeys": [random.choice(categories)],
        }
        self.client.get("/api/data/", params=params)

    @task(3)
    def view_cart(self):
        self.client.get("/api/cart")

    @task(2)
    def add_to_cart(self, user=""):
        if user == "":
            user = str(uuid.uuid1())
        product = random.choice(products)
        self.client.get("/api/products/" + product)
        cart_item = {
            "item": {
                "productId": product,
                "quantity": random.choice([1, 2, 3, 4, 5, 10]),
            },
            "userId": user,
        }
        self.client.post("/api/cart", json=cart_item)

    @task(1)
    def checkout(self):
        # checkout call with an item added to cart
        user = str(uuid.uuid1())
        self.add_to_cart(user=user)
        checkout_person = random.choice(people)
        checkout_person["userId"] = user
        self.client.post("/api/checkout", json=checkout_person)

    @task(1)
    def checkout_multi(self):
        # checkout call which adds 2-4 different items to cart before checkout
        user = str(uuid.uuid1())
        for i in range(random.choice([2, 3, 4])):
            self.add_to_cart(user=user)
        checkout_person = random.choice(people)
        checkout_person["userId"] = user
        self.client.post("/api/checkout", json=checkout_person)

    @task(5)
    def flood_home(self):
        for _ in range(0, 10):
            self.client.get("/")

    def on_start(self):
        ctx = baggage.set_baggage("session.id", str(uuid.uuid4()))
        ctx = baggage.set_baggage("synthetic_request", "true", context=ctx)
        context.attach(ctx)
        self.index()


browser_traffic_enabled = os.environ.get("LOCUST_BROWSER_TRAFFIC_ENABLED", "").lower() in ("true", "yes", "on")

if browser_traffic_enabled:
    class WebsiteBrowserUser(PlaywrightUser):
        headless = True  # to use a headless browser, without a GUI
        wait_time = between(5, 10)

        @task
        @pw
        async def open_cart_page_and_change_currency(self, page: PageWithRetry):
            try:
                page.on("console", lambda msg: print(msg.text))
                await page.route('**/*', add_baggage_header)
                await page.goto("/cart", wait_until="domcontentloaded")
                await page.select_option('[name="currency_code"]', 'CHF')
                await page.wait_for_timeout(6000)  # giving the browser time to export the traces
            except:
                pass

        @task
        @pw
        async def add_product_to_cart(self, page: PageWithRetry):
            try:
                page.on("console", lambda msg: print(msg.text))
                await page.route('**/*', add_baggage_header)
                await page.goto("/", wait_until="domcontentloaded")
                await page.click('p:has-text("Roof Binoculars")', wait_until="domcontentloaded")
                await page.click('button:has-text("Add To Cart")', wait_until="domcontentloaded")
                await page.wait_for_timeout(6000)  # giving the browser time to export the traces
            except:
                pass


async def add_baggage_header(route: Route, request: Request):
    existing_baggage = request.headers.get('baggage', '')
    headers = {
        **request.headers,
        'baggage': ', '.join(filter(None, (existing_baggage, 'synthetic_request=true')))
    }
    await route.continue_(headers=headers)
