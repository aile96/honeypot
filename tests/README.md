
## `tests/README.md`

```markdown
# Tests

This directory contains the pytest-based test suite for the lab, controller, and orchestration logic.

The tests intentionally focus on the infrastructure that starts, configures, monitors, and cleans up the laboratory environment.

They do not test the internal business logic of the demo services deployed inside the lab.

## Scope

The test suite covers:

- host-side bootstrap logic;
- configuration parsing;
- runtime state handling;
- template rendering;
- controller pipeline behavior;
- Caldera ability/adversary mapping;
- cleanup behavior;
- proxy/controller helper logic;
- static assumptions used by the lab orchestration code.

The test suite does not cover:

- application-level service behavior inside `src/opentelemetry/containers`;
- application-level service behavior inside `src/5Gcore`;
- frontend behavior;
- payment, cart, checkout, product catalog, recommendation, or similar demo services;
- real attacker behavior outside the local lab;
- external networks or public targets.

## Test runner

The main test runner is `pytest`.

Install development dependencies from the repository root:

```bash
python3 -m pip install -r requirements-dev.txt