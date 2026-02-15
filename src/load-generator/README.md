# Load Generator

The load generator creates simulated traffic to the demo.

## Accessing the Load Generator

You can access the web interface to Locust at `http://localhost:8080/loadgen/`.

## Traffic Model

The load generator now uses multiple stateful user personas:

- `AnonymousBrowserUser`
- `ReturningBuyerUser`
- `AuthenticatedUser`
- `PowerUser`
- `WebsiteBrowserUser` (Playwright, optional)

Each persona keeps session state (`session.id`, cart, currency, optional auth token), and together they cover:

- `GET /`
- `GET /api/products`
- `GET /api/products/{id}`
- `GET /api/recommendations`
- `GET /api/data`
- `GET /api/cart`
- `POST /api/cart`
- `DELETE /api/cart`
- `POST /api/checkout`
- `GET /api/currency`
- `GET /api/shipping`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/verify`

## Configuration

The following environment variables can be used to tune behavior:

- `LOCUST_WAIT_MIN_SECONDS` (default `1.5`)
- `LOCUST_WAIT_MAX_SECONDS` (default `6.0`)
- `LOCUST_CURRENCY_MIX` (default `USD,EUR,CHF,CAD`)
- `LOCUST_NEGATIVE_TESTS_ENABLED` (`true|false`, default `false`)
- `LOCUST_NEGATIVE_TEST_RATE` (default `0.03`)
- `LOCUST_AUTH_RATIO` (`0.0` to `1.0`, optional)
- `LOCUST_CHECKOUT_MULTI_ITEM_MAX` (default `4`)
- `LOCUST_BOOTSTRAP_TIMEOUT_SECONDS` (default `5`)
- `LOCUST_BROWSER_TRAFFIC_ENABLED` (`true|false`, default `true`)
- `LOCUST_WEIGHT_ANONYMOUS_BROWSER`
- `LOCUST_WEIGHT_RETURNING_BUYER`
- `LOCUST_WEIGHT_AUTHENTICATED_USER`
- `LOCUST_WEIGHT_POWER_USER`
- `LOCUST_WEIGHT_BROWSER_TRAFFIC`
- `LOCUST_SCENARIO_MIX` (example: `anonymous:8,returning:4,authenticated:2,power:1,browser:1`)

## Dynamic Catalog Bootstrap

At test start, product IDs and categories are bootstrapped from `GET /api/products`.
If bootstrap fails, the generator automatically falls back to a built-in catalog.

## KPI Output

At test stop, the generator logs:

- endpoint coverage over tracked API surface
- cart-to-checkout conversion (`checkout_success / add_to_cart_success`)
- baggage injection ratio
- per-endpoint error rate (`failures / total`)

## Modifying the Load Generator

Please see the [Locust
documentation](https://docs.locust.io/en/2.16.0/writing-a-locustfile.html) to
learn more about modifying the locustfile.
