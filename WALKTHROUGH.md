# Per-file walkthrough

Each entry: **Purpose · Flow · Key functions · Say to the interviewer.**

---
## config/settings.py
**Purpose:** all configuration, env-driven (`DJANGO_SECRET_KEY`, `DATABASE_PATH`, `MODEL_DIR`, `LOG_LEVEL`).
**Flow:** loaded by Django at start; DRF is set to JSON-only, no auth/session (so no CSRF), and a custom exception handler.
**Key:** `_flag()` parses booleans; `MODEL_DIR` is read at call time by the registry so tests can override it.
**Say:** "12-factor config; I removed admin/auth/sessions because the brief has no accounts — smaller attack surface and faster startup. `APPEND_SLASH=False` with routes accepting an optional slash because the brief mixes `/api/outbox` and `/api/purchases/`."

## config/urls.py, config/wsgi.py, manage.py
**Purpose:** mount `shop.urls` under `/api/`; WSGI entry; CLI entry.
**Say:** "Boilerplate kept minimal on purpose."

## shop/models.py
**Purpose:** schema. `Item`, `Extra` (M2M to items → "cushion only for stool" is data, not code), `Customer`, `Order`, `OutboxMessage`, `HistoricalOrder`.
**Flow:** `Order` is the live ledger; `HistoricalOrder` is separate and never touches stock.
**Key:** `UniqueConstraint(customer, item)` backs the duplicate rule; `external_charge_id` unique backs idempotency; indexes `(created_at, order_id)` for pagination order and `(sku, created_at)` for stats.
**Say:** "Invariants live in the DB as well as in code, so concurrent requests can't violate them. Money is integer cents — no float rounding."

## shop/migrations/0001_initial.py, 0002_seed_catalog.py
**Purpose:** schema; data migration seeding lamp (8000, 2), stool (12000, 1), cushion (2500, stool only).
**Key:** `seed()` uses `update_or_create`, so it's idempotent.
**Say:** "Seeding via migration means `migrate` is the only setup step."

## shop/errors.py
**Purpose:** exception hierarchy. `DomainError(code, http_status)` and subclasses (`SoldOut`, `DuplicatePurchase` → 409; `InvalidChargeId`, `UnknownItem`, `ExtraNotAllowed`, `MissingFields`, `InvalidInput` → 400; `ModelNotTrained` → 503).
**Say:** "Services raise typed errors with no HTTP knowledge beyond a status hint; no bare excepts anywhere."

## shop/api_errors.py
**Purpose:** single DRF exception handler → `{"error": code}`; malformed JSON → `invalid_json`; 404 → `not_found`.
**Say:** "One place defines the error contract."

## shop/validation.py
**Purpose:** pure normalisation/validation shared by API, importer, predictor.
**Key:** `normalise_email/token/optional`; `is_valid_charge_id` (`fullmatch` avoids `$`-before-newline pitfall); `is_valid_email`; `parse_iso_datetime` (regex requires `T` + `Z`/offset, then `fromisoformat`, converted to UTC — rejects date-only, naive and impossible dates like June 31); `parse_bool`; `parse_query_date` (strict `YYYY-MM-DD`).
**Say:** "Stdlib `fromisoformat` is too lenient (accepts date-only, naive), so a strict regex guards it. Sharing validators prevents the API and the importer drifting."

## shop/services/purchases.py
**Purpose:** the purchase use-case; every rule, in the specified order, in one transaction.
**Flow:** validate charge id → replay lookup (return original, `replayed=True`, write nothing) → item → extra → stock → duplicate → `_commit_purchase` in a savepoint.
**Key:** `record_purchase()` orchestrates; `_find_replay()` fetches with `select_related`; `_commit_purchase()` does conditional stock `UPDATE ... remaining > 0`, `get_or_create` customer, creates the order (price = item + extra), then bulk-inserts outbox rows (`account_created` first only if customer is new).
**Say:** "Atomicity: any `DomainError` rolls back customer/outbox/stock together. The conditional UPDATE means stock can't go negative even without row locks. If two requests share a charge id, the loser hits `IntegrityError`, the savepoint rolls back its partial writes and it returns the winner's order as a replay. Outbox rows are written in the same transaction (transactional outbox pattern) so we never send a message for a rolled-back purchase. At scale: Postgres + `select_for_update`, relay worker for the outbox."

## shop/views.py, shop/urls.py
**Purpose:** HTTP adapters. `PurchaseView` (201/200), `CatalogView`, `OutboxView` (ordered by id), `HistoryView`, `HistoryStatsView`, `ReturnRiskView`.
**Key:** `_required_str/_optional_str` shape input; `order_payload` is the API contract for orders.
**Say:** "Views contain no business rules. Replay → 200 vs 201 decided only by the flag the service sets."

## shop/catalog.py
**Purpose:** `PriceCatalog` snapshot (two queries) with `total_for(sku, extra)` returning the expected total or `None` if not sellable.
**Say:** "Importer and predictor derive allowed combos and prices from the DB, not hard-coded constants, so there's one source of truth shared with purchases."

## shop/services/importer.py
**Purpose:** re-runnable CSV import with first-failure rejection reasons.
**Flow:** stream rows → file-level duplicate check → `validate_row` in the specified order → batches of 500 → one `IN` query for existing ids → `bulk_create` the fresh ones → JSON summary. Whole run is one transaction.
**Key:** `validate_row`, `import_rows`, `import_csv` (utf-8-sig, `newline=""`), `_chunks`.
**Say:** "No query per row: 2 queries per 500 rows. Idempotent: second run reports `skippedExisting`. Edge cases: BOM, whitespace/case in sku/email/channel, blank extra = none, offsets converted to UTC, stray `maybe` for returned. Memory: `seen` set is O(n); at 10M rows I'd stage into a table with a unique index and use `ON CONFLICT DO NOTHING`."

## shop/history.py
**Purpose:** read queries. `filter_history` (inclusive UTC date range as `>= from 00:00` and `< to+1 day`, index-friendly), `paginate` (10/page ordered by `created_at, order_id`), `sku_stats` (single `GROUP BY` with `Count`, `Sum`, filtered `Count`), `_rate` (half-up to 2 dp via `Decimal`).
**Say:** "Aggregation is in the DB as required. Half-up `Decimal` avoids float banker's-rounding surprises. At scale: keyset pagination and rollup tables."

## shop/ml/features.py
**Purpose:** feature definition shared by training and serving.
**Key:** `feature_vector` (is_stool, has_extra, channel_app, channel_web with store baseline, UTC hour, prior_orders); `build_training_set` computes `prior_orders` as strictly earlier orders per email using `groupby` + `bisect_left` (O(n log k)).
**Say:** "Same function for train and serve eliminates skew. Ties at the same timestamp don't count as 'earlier', matching the serving query `created_at < t`."

## shop/ml/training.py
**Purpose:** `train()` — builds data, stratified 80/20 split (`random_state=0`), `LogisticRegression(max_iter=1000, random_state=0)`, computes holdout and majority-class baseline accuracy, writes artifact + `metrics.json`.
**Key:** `_atomic_write` (temp file + `os.replace`); version = UTC timestamp + random suffix so it changes every run; `TrainingError` if a class has <2 rows.
**Say:** "Atomic writes mean the server never reads a half-written model. Accuracy isn't scored, but I report the baseline so the number is honest: on this tiny noisy data the model doesn't beat majority-class, and I say so."

## shop/ml/registry.py
**Purpose:** process-wide cache; `get()` loads the joblib once and re-loads only if `(path, mtime, size)` changed; thread-safe.
**Say:** "Per request cost is one `stat`, not a deserialise. Retraining in another process is picked up without restart. joblib/pickle must only come from a trusted path."

## shop/ml/predict.py
**Purpose:** validate request, look up model (503 if none), count prior orders for that email before `createdAt`, return `probability` (3 dp) + `modelVersion`.
**Say:** "Validation precedes the model check so bad input is always a 400. The class index is looked up from `classes_`, not assumed."

## shop/management/commands/import_history.py, train_return_model.py
**Purpose:** CLI wrappers; print exactly one JSON line to stdout (logs go to stderr); turn expected failures into `CommandError`.

## shop/tests/test_purchases.py
**Covers:** new customer + outbox order, extra in total, sold-out leaves no customer, duplicate, extra not sold, unknown sku/invalid charge id write nothing, replay with different sku/extra/email, rollback snapshot equality, HTTP worked examples and 400/404.
**Say:** "`snapshot()` compares stock, customers, orders, outbox before/after every failure."

## shop/tests/test_history.py
**Covers:** CSV import counts per reason and no-op rerun, normalisation, row-rule order, pagination, filters, inclusive date bounds, May 2026 stats vs. hand count, `assertNumQueries` on stats.
**Say:** "Expected numbers came from a separate throw-away script, not from the code under test."

## shop/tests/test_prediction.py
**Covers:** 503 before training, probability/version after training (trained in setUp), metrics file schema, version changes per run and is picked up, model loaded once, validation errors, `prior_orders` tie semantics, training without data.

## requirements.txt, Makefile, Dockerfile, .env.example, .gitignore, data/order_history.csv
**Purpose:** reproducible setup (`make all`), container, config template, ignored artifacts (db, trained models), and the provided dataset.
