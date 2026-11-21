# User Guide

Complete reference for running, using and understanding the Purchase Recorder + Return-Risk service.
Commands are PowerShell unless marked otherwise. Run everything from the project root.

## Contents
1. [What the system does](#1-what-the-system-does)
2. [Architecture](#2-architecture)
3. [Setup and run order](#3-setup-and-run-order)
4. [Management commands](#4-management-commands)
5. [HTTP API reference](#5-http-api-reference)
6. [Business rules](#6-business-rules)
7. [Data and file layout](#7-data-and-file-layout)
8. [Configuration](#8-configuration)
9. [Testing](#9-testing)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What the system does
- **Part A, record a purchase.** A customer buys an item (lamp or stool), optionally with an add-on (cushion, stool only). Stock, duplicate and replay rules are enforced in one transaction. A notification row is written to an outbox table instead of sending email.
- **Part B, learn from past orders.** Old orders are imported from CSV, browsed and aggregated through the API, and used to train a logistic-regression model that estimates the probability a new order will be returned.

Seed data (created by `migrate`):

| SKU | Name | Price (cents) | Stock |
|---|---|---|---|
| `lamp` | Maple lamp | 8000 | 2 |
| `stool` | Cedar stool | 12000 | 1 |
| extra `cushion` (stool only) | Cushion | 2500 | n/a |

All money is integer cents (a stool with cushion is 14500).

---

## 2. Architecture

### 2.1 Layers and request flow
```
                         ┌──────────────────────────────────────┐
 HTTP client ──────────► │ config/urls.py  →  shop/urls.py      │  routing (optional trailing slash)
                         └───────────────┬──────────────────────┘
                                         ▼
                         ┌──────────────────────────────────────┐
                         │ shop/views.py (thin HTTP adapters)   │  parse JSON, shape response
                         └──────┬───────────┬──────────┬────────┘
                                ▼           ▼          ▼
              services/purchases.py   history.py   ml/predict.py       business logic
              (rules, 1 transaction)  (filter,     (validate, features,
                                       paginate,    registry → model)
                                       GROUP BY)
                                ▼           ▼          ▼
                         ┌──────────────────────────────────────┐
                         │ shop/models.py  →  SQLite            │  persistence + constraints
                         └──────────────────────────────────────┘

 CLI ─ import_history ───► services/importer.py ─► validation.py, catalog.py ─► HistoricalOrder
 CLI ─ train_return_model ► ml/training.py ─► features.py ─► models/return_model.joblib + metrics.json
                                                                   │
                           ml/registry.py  (loads once, reloads on file change) ◄──┘
```

| Layer | Files | Responsibility |
|---|---|---|
| Interface | `views.py`, `urls.py`, `api_errors.py`, `management/commands/*` | Parse input, call logic, shape output. No business rules. |
| Application logic | `services/purchases.py`, `services/importer.py`, `history.py`, `ml/predict.py`, `ml/training.py` | Use-cases, transactions, queries. |
| Domain helpers (pure) | `validation.py`, `catalog.py`, `ml/features.py`, `errors.py` | Normalisation, validation, price rules, feature encoding. No HTTP. |
| Storage | `models.py`, `migrations/` | Schema, constraints, indexes, seed data. |

### 2.2 Data model
```
Item ─┬─< Order >─── Customer          Order.external_charge_id  UNIQUE   (idempotency)
      │     └── Extra (nullable)       Order (customer,item)     UNIQUE   (one purchase per item)
Extra >─< Item (M2M: which extras an item sells)
OutboxMessage (to_email, kind, external_charge_id)      standalone, written in the order's transaction
HistoricalOrder (order_id UNIQUE, email, sku, ...)      standalone, never touches stock
```

### 2.3 Key design decisions
| Decision | Why |
|---|---|
| All purchase rules in one `atomic()` service | A failed rule rolls back customer, order, stock and outbox together. "Sold out never leaves a customer row" holds by construction. |
| Conditional stock update `UPDATE … WHERE remaining > 0` | Stock cannot go negative even if two requests race, and it needs no row lock (works on SQLite). |
| Unique `external_charge_id`, inner savepoint on `IntegrityError` | Retries and concurrent duplicates become a replay instead of a 500 or a double order. |
| `UNIQUE(customer, item)` | The duplicate-purchase rule also holds at the database level. |
| Outbox table written in the same transaction | A message exists if and only if the purchase committed (transactional outbox). A separate worker could relay it later. |
| Extras stored as data (M2M) | "Cushion only for stool" is configuration, not an `if` in code; importer and predictor reuse it via `PriceCatalog`. |
| Streaming batch import (500 rows) | Two queries per batch (`IN` check and `bulk_create`), never one per row; one transaction, so a crash leaves nothing. |
| Stats via one `GROUP BY` | Counting and summing happen in the database, not Python loops. |
| One feature function for train and serve | Prevents train/serve skew. |
| Model registry keyed on file `(path, mtime, size)` | Model is deserialised once, retraining elsewhere is picked up without a restart; cost per request is a single `stat()`. |
| Atomic artifact writes (temp file + `os.replace`) | The server never reads a half-written model. |
| Typed error hierarchy, one handler | Services raise `DomainError` subclasses, and `api_errors.py` is the only place that maps them to `{"error": code}` and an HTTP status. |

### 2.4 Scaling notes
At about 10 M history rows: move to Postgres; keep `(sku, created_at)` for stats or add daily rollups; use keyset pagination instead of `OFFSET`/`COUNT(*)`; replace the importer's in-memory `seen` set with a staging table and `ON CONFLICT DO NOTHING`; compute `prior_orders` with a SQL window function; run training as a job with versioned artifacts in object storage.

---

## 3. Setup and run order

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1                      # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
pip install -r requirements.txt                 # 1. dependencies (Python 3.12+)
python manage.py migrate                        # 2. schema + seed items
python manage.py import_history data/order_history.csv   # 3. import history
python manage.py train_return_model             # 4. train (needs step 3)
python manage.py runserver 8123                 # 5. start API (keep this window open)
```
Order matters: **import before train**, and train before calling the prediction endpoint. Purchases, catalog and outbox work right after `migrate`.

If `make` is installed, `make all` runs install → migrate → import → train → test.

---

## 4. Management commands

| Command | Significance and usage | Notes |
|---|---|---|
| `python manage.py migrate` | Creates tables and seeds lamp, stool and cushion (data migration `0002`). Re-running is safe. | Resetting: delete `db.sqlite3` and run again. |
| `python manage.py import_history <csv>` | Validates and loads historical orders. Prints exactly one JSON line: `{"created":N,"skippedExisting":N,"rejected":{"<reason>":N}}`. | Safe to re-run: existing `order_id`s are skipped, so the table is unchanged. Logs go to stderr. |
| `python manage.py train_return_model` | Trains the model on all imported history, writes `models/return_model.joblib` and `models/metrics.json`, and prints the metrics JSON. | `version` changes every run. Fails with a clear message if there is too little data or only one class. |
| `python manage.py runserver [port]` | Starts the dev server (default 8000). | Use `8123` if Windows blocks 8000. Keep the window open. Use a second window for requests. |
| `python manage.py test` | Runs the 30-test suite on a throwaway database. | Does not touch `db.sqlite3` or your trained model. |
| `python manage.py check` | Validates configuration. | Useful before running. |
| `python manage.py makemigrations` / `showmigrations` | Standard Django schema tooling. | Only needed if you change `models.py`. |
| `make install \| setup \| import \| train \| run \| test \| all` | Shortcuts for the commands above. | Requires `make`. |

### Import rejection reasons
Checked in this order; the first failure wins.

| Reason | Meaning |
|---|---|
| `duplicate_order_id` | `order_id` appeared on an earlier line of this file (even if that line was rejected). |
| `invalid_email` | After trim and lowercase: not text, not exactly one `@`, empty side, or contains whitespace. |
| `unknown_sku` | Not `lamp` or `stool` after trim and lowercase. |
| `extra_not_allowed` | Extra set (blank means none) and not `cushion` on `stool`. |
| `bad_total` | Not an integer, or not item price plus extra price (8000, 12000, 14500). |
| `bad_date` | Not ISO 8601 date-time with `Z` or an offset, or an impossible date. Stored in UTC. |
| `bad_returned` | Not one of 0, 1, true, false, yes, no (case-insensitive). |
| `bad_channel` | Not `web`, `app` or `store` after lowercasing. |

A valid row whose `order_id` is already in the database is counted in `skippedExisting`, not rejected.

### Model details
Logistic regression (`max_iter=1000`, `random_state=0`), 80/20 stratified split (`random_state=0`).
Features: `is_stool`, `has_extra`, `channel_app`, `channel_web` (store is the baseline), `hour` (UTC 0 to 23), `prior_orders` (earlier orders by the same email).
`metrics.json`: `version`, `rows`, `trainRows`, `holdoutAccuracy`, `baselineAccuracy` (always predict the most common holdout class), `features`.

---

## 5. HTTP API reference
Base URL, for example `http://127.0.0.1:8123`. JSON in and out. Every route accepts an optional trailing slash. Errors are always `{"error": "<code>"}`.

### POST `/api/purchases/`
Records a purchase (idempotent per `externalChargeId`).
```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8123/api/purchases/ -ContentType application/json -Body '{"email":"ann@example.com","sku":"stool","externalChargeId":"ch_stool0001","extraCode":"cushion"}'
```
Body: `email`, `sku`, `externalChargeId` (required); `extraCode` (optional).

| Status | Body | When |
|---|---|---|
| 201 | `{"replayed":false,"order":{id,email,sku,extraCode,externalChargeId,totalCents}}` | New order created. |
| 200 | `{"replayed":true,"order":{…original…}}` | Charge id already processed. Nothing is written. |
| 409 | `{"error":"sold_out"}` / `{"error":"duplicate_purchase"}` | Stock is 0, or this customer already bought this sku. |
| 400 | `{"error":"unknown_item"}`, `extra_not_allowed`, `invalid_charge_id`, `missing_fields`, `invalid_json` | Bad input. |

`externalChargeId` must match `^ch_[A-Za-z0-9]{8,}$`.

### GET `/api/catalog/<sku>`
Returns `{"sku","name","priceCents","remaining"}`. Unknown sku gives 404 `{"error":"not_found"}`.

### GET `/api/outbox`
Returns the notification rows in creation order: `[{"toEmail","kind","externalChargeId"}]`. Kinds: `account_created` (only for a new customer, written first), then `purchase_recorded`.

### GET `/api/history/`
Imported orders, 10 per page, ordered by `createdAt` then `orderId`.
Query params (all optional): `sku`, `channel`, `from`, `to` (`YYYY-MM-DD`, inclusive, compared to the UTC date of `createdAt`), `page` (1 or more).
Response: `{"count","page","pageSize","results":[{orderId,email,sku,extraCode,channel,createdAt,totalCents,returned}]}`.
Bad date gives 400 `invalid_date`. Bad page gives 400 `invalid_page`. A page beyond the end returns an empty `results`.
```powershell
Invoke-RestMethod "http://127.0.0.1:8123/api/history/?sku=stool&channel=web&from=2026-05-01&to=2026-05-31&page=1"
```

### GET `/api/history/stats?from=&to=`
Per-sku aggregates, computed in the database: `{"lamp":{"orders","revenueCents","returnRate"},"stool":{…}}`. `returnRate` is returned divided by orders, rounded half-up to 2 decimals, or `null` with no orders.
```powershell
Invoke-RestMethod "http://127.0.0.1:8123/api/history/stats?from=2026-05-01&to=2026-05-31"
```

### POST `/api/predictions/return-risk`
```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8123/api/predictions/return-risk -ContentType application/json -Body '{"email":"ada@example.com","sku":"stool","extraCode":"cushion","channel":"app","createdAt":"2026-07-01T10:00:00Z"}'
```
200 gives `{"probability":0.412,"modelVersion":"<version>"}` (probability in [0, 1], 3 decimals).
400 codes: `unknown_item`, `extra_not_allowed`, `bad_channel`, `bad_date`, `invalid_email`, `missing_fields`. Validation runs before the model check.
503 `{"error":"model_not_trained"}` when `train_return_model` has not been run.

### Quick end-to-end demo
```powershell
# purchase, replay (same charge id, different sku), duplicate, sold out
$u = "http://127.0.0.1:8123/api/purchases/"
function Buy($b) { try { Invoke-RestMethod -Method Post -Uri $u -ContentType application/json -Body $b } catch { $_.ErrorDetails.Message } }
Buy '{"email":"ann@example.com","sku":"lamp","externalChargeId":"ch_lamp00001"}'     # 201
Buy '{"email":"ann@example.com","sku":"stool","externalChargeId":"ch_lamp00001"}'    # 200 replay, still lamp
Buy '{"email":"ann@example.com","sku":"lamp","externalChargeId":"ch_lamp00002"}'     # 409 duplicate_purchase
Buy '{"email":"bob@example.com","sku":"stool","externalChargeId":"ch_stool0001","extraCode":"cushion"}'  # 201, 14500
Buy '{"email":"cy@example.com","sku":"stool","externalChargeId":"ch_stool0002"}'      # 409 sold_out
Invoke-RestMethod http://127.0.0.1:8123/api/outbox
```
To repeat the demo, delete `db.sqlite3`, then run `migrate`, `import_history` and `train_return_model` again.

---

## 6. Business rules
Evaluated in this order for `POST /api/purchases/`:
1. Charge id format, else `invalid_charge_id` (nothing written).
2. **Replay**: charge id already has an order, so return it with `replayed: true`. The new sku, extra and email are ignored. No stock change and no outbox row.
3. Unknown sku, so `unknown_item`.
4. Extra not sold with the item, so `extra_not_allowed`.
5. Stock is 0, so `sold_out` (a new email leaves no customer or outbox row).
6. Same customer already bought this sku, so `duplicate_purchase`.
7. **Success**: create the customer if new, create the order, decrement stock, total = item price plus extra price, then write `account_created` (new customers only) followed by `purchase_recorded`.

The email is trimmed and lowercased before any lookup, and all writes commit or roll back together.

---

## 7. Data and file layout
```
config/                 Django project (settings, urls, wsgi)
shop/models.py          schema
shop/services/          purchases.py (use-case), importer.py (CSV import)
shop/history.py         list, filter, paginate, stats queries
shop/ml/                features.py, training.py, registry.py, predict.py
shop/validation.py      pure validators/normalisers
shop/catalog.py         price/extras snapshot
shop/tests/             purchases, history/import, prediction tests
data/order_history.csv  provided dataset (122 rows)
models/                 return_model.joblib + metrics.json (created by training, git-ignored)
db.sqlite3              database (created by migrate, git-ignored)
```
Per-file detail and interview talking points are in `WALKTHROUGH.md`.

---

## 8. Configuration
Environment variables (see `.env.example`). All are optional.

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev-only key | Set a real value outside development. |
| `DJANGO_DEBUG` | `1` | Set `0` in production. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,testserver` | Comma-separated hosts. |
| `DATABASE_PATH` | `db.sqlite3` | SQLite file location. |
| `MODEL_DIR` | `models` | Where the model and metrics are written and read. |
| `LOG_LEVEL` | `INFO` | Logging level for the `shop` logger. |

PowerShell example: `$env:LOG_LEVEL="WARNING"; python manage.py runserver 8123`.

---

## 9. Testing
```powershell
python manage.py test                              # all 30 tests
python manage.py test shop.tests.test_purchases    # one module
python manage.py test shop.tests.test_history.ImportTests.test_import_counts_and_rerun_is_noop   # one test
```
Coverage: sold out (new email not saved), duplicate customer and sku, extra not sold, replay with a different sku, customer and outbox creation, total with extra, failed purchases leaving state unchanged, CSV import counts per reason with a no-op second run, May 2026 stats against hand counts, 503 before training, and probability and version after training.

---

## 10. Troubleshooting
| Symptom | Cause and fix |
|---|---|
| `You don't have permission to access that port` | Windows reserved the port. Use `runserver 8123`, or check `netsh interface ipv4 show excludedportrange protocol=tcp`. |
| `Unable to connect to the remote server` | The server is not running. Start `runserver` in one window and keep it open. Send requests from another window. |
| Browser shows 404 at `/` | Expected. There is no home page, so use `/api/...` URLs. |
| `503 model_not_trained` | Run `import_history`, then `train_return_model`. |
| `no such table` | Run `python manage.py migrate`. |
| Activation blocked in PowerShell | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`, then activate again. |
| Want a clean slate | Delete `db.sqlite3` and `models\*.joblib`, then repeat section 3. |
