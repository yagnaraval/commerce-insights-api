# Commerce Insights API

> **Author & Attribution**
>
> **Name:** Yagna Raval
> **LinkedIn:** [https://www.linkedin.com/in/yagna-raval/](https://www.linkedin.com/in/yagna-raval/)
> **GitHub:** [https://github.com/yagnaraval](https://github.com/yagnaraval)

## Purchase Recorder + Return-Risk Model

A Django REST Framework service that:

- Records purchases under strict transactional rules.
- Imports historical orders from CSV.
- Provides reporting and statistics endpoints.
- Trains and serves a scikit-learn return-risk prediction model.

## Problem Statement

A small shop sells two items with limited stock and one add-on. A payment provider may retry the same charge, two customers may race for the last available unit, and historical orders arrive as a CSV file that may contain invalid data.

The application provides correct, idempotent purchase recording, prevents partial writes, and supports repeatable historical imports that feed reporting and a simple return-risk prediction model.

## Architecture

```text
HTTP ──► views.py (parse/shape JSON) ──► services/purchases.py ──► models (SQLite)
                  │                              (transactional rules)
                  │
                  ├──► history.py (filter / paginate / GROUP BY stats)
                  │
CLI ──► import_history ──► services/importer.py ──► validation.py
CLI ──► train_return_model ──► ml/training.py ──► model artifacts
HTTP ──► ml/predict.py ──► ml/registry.py (load once, reload on file change)
                         └──► ml/features.py (shared by train + serve)
```

**Application layers:**

- `views.py`: Thin API views that parse requests and shape responses.
- `services/`: Purchase and historical import business logic.
- `history.py`: Historical order filtering, pagination, and database aggregations.
- `ml/`: Model training, prediction, feature generation, and artifact management.
- `models.py`: Database models.
- `validation.py`: Reusable input validation.

Errors use a typed hierarchy (`errors.py`) and are mapped to API responses in one place (`api_errors.py`).

## Key Design Trade-offs

- **Transactional purchase recording:** `record_purchase` evaluates purchase rules inside a database transaction. Domain errors roll back all writes, ensuring failed purchases do not leave behind customers, orders, or outbox messages.

- **Stock protection:** Stock is decremented using a conditional database update (`UPDATE ... WHERE remaining > 0`) rather than relying only on an application-level stock check.

- **Idempotency:** The unique `external_charge_id` constraint prevents duplicate processing. A concurrent uniqueness conflict is handled through an inner savepoint and treated as a replay.

- **Database constraints:** `UNIQUE(customer, item)` provides a database-level backstop for duplicate purchases.

- **Batch historical import:** CSV rows are processed in batches of 500, using one existing-ID query and `bulk_create` per batch inside a transaction. Re-running the importer skips existing `order_id` values.

- **Database-side statistics:** Reporting uses database aggregation (`Count`, `Sum`, and filtered `Count`) rather than loading all historical records into Python.

- **Model serving:** The model registry loads the joblib artifact once and checks file metadata (`mtime` and size) to detect retraining. Model artifacts are written atomically.

- **Shared feature logic:** The same feature-generation code is used during training and prediction to avoid training-serving inconsistencies.

## Quickstart

**Requirements:**

- Python 3.12+
- pip
- SQLite (included with Python)
- Docker (optional)

### 1. Clone the repository

```bash
git clone https://github.com/yagnaraval/commerce-insights-api.git
cd commerce-insights-api
```

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Set up the database

```bash
python manage.py migrate
```

This creates the database schema and seeds the catalogue:

- Maple lamp — 8,000 cents, stock 2
- Cedar stool — 12,000 cents, stock 1
- Cushion add-on — 2,500 cents, available for the stool

### 5. Import historical orders

```bash
python manage.py import_history data/order_history.csv
```

### 6. Train the return-risk model

```bash
python manage.py train_return_model
```

Historical data must be imported before training.

### 7. Start the development server

```bash
python manage.py runserver
```

The API is available at:

```text
http://127.0.0.1:8000/
```

### 8. Run tests

```bash
python manage.py test
```

### Makefile shortcut

The following command runs the setup, import, training, and test steps:

```bash
make all
```

It runs:

```text
install → migrate → import → train → test
```

## Docker

Docker is supported for running the application without manually setting up the Python environment.

Build the image:

```bash
docker build -t shop .
```

Run the container:

```bash
docker run --rm -p 8000:8000 shop
```

The container entrypoint automatically:

1. Applies database migrations.
2. Imports `data/order_history.csv`.
3. Trains the return-risk model.
4. Starts the Django development server.

The API is then available at `http://127.0.0.1:8000/`.

Configuration is managed through environment variables. See `.env.example`.

## Example API Usage

### Record a purchase

```bash
curl -X POST http://localhost:8000/api/purchases/ \
  -H 'Content-Type: application/json' \
  -d '{"email":"Ann@Example.com","sku":"lamp","externalChargeId":"ch_lamp00001"}'
```

### Get catalogue item details

```bash
curl http://localhost:8000/api/catalog/lamp
```

### Get outbox messages

```bash
curl http://localhost:8000/api/outbox
```

### Get historical orders

```bash
curl 'http://localhost:8000/api/history/?sku=stool&channel=web&from=2026-05-01&to=2026-05-31&page=1'
```

### Get historical statistics

```bash
curl 'http://localhost:8000/api/history/stats?from=2026-05-01&to=2026-05-31'
```

Example response:

```json
{
  "lamp": {
    "orders": 43,
    "revenueCents": 344000,
    "returnRate": 0.28
  },
  "stool": {
    "orders": 20,
    "revenueCents": 265000,
    "returnRate": 0.3
  }
}
```

Every catalogue SKU appears in the statistics response. `returnRate` is `null` when there are no orders for that SKU.

### Predict return risk

```bash
curl -X POST http://localhost:8000/api/predictions/return-risk \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","sku":"stool","extraCode":"cushion","channel":"app","createdAt":"2026-07-01T10:00:00Z"}'
```

The prediction endpoint returns a probability between 0 and 1, rounded to three decimal places, along with the model version.

## Decisions on Ambiguous Requirements

- Prediction input is validated first (400) before checking whether a trained model exists (503).
- `duplicate_order_id` counts any earlier line with the same ID, even if that earlier line was rejected.
- Charge IDs are validated before replay lookup and are not trimmed.
- Prediction validation errors include `unknown_item`, `extra_not_allowed`, `bad_channel`, `bad_date`, and `invalid_email`.

## Historical Import Results

The supplied `order_history.csv` contains 122 rows.

Expected import summary:

| Result | Count |
|---|---:|
| Imported records | 112 |
| Rejected records | 10 |

Rejected records:

| Rejection reason | Count |
|---|---:|
| `duplicate_order_id` | 2 |
| `invalid_email` | 2 |
| `unknown_sku` | 1 |
| `extra_not_allowed` | 1 |
| `bad_total` | 1 |
| `bad_date` | 2 |
| `bad_returned` | 1 |

The May 2026 statistics were independently checked against the supplied CSV:

| SKU | Orders | Revenue (cents) | Returned | Return rate |
|---|---:|---:|---:|---:|
| lamp | 43 | 344000 | 12 | 0.28 |
| stool | 20 | 265000 | 6 | 0.30 |

These expected values were calculated using a separate temporary script rather than the application's implementation.

## Time Spent

Approximately **6 hours**, covering requirement analysis, project architecture, implementation, testing, debugging, and documentation.

## AI Tools

**ChatGPT (GPT-6)** was used as a development and review assistant.

It helped with:

- Breaking down the requirements and reviewing implementation decisions.
- Discussing edge cases involving database transactions, idempotency, CSV validation, and ML feature consistency.
- Reviewing test coverage, identifying potential improvements, and refining documentation.

All suggestions were reviewed against the actual assignment requirements before being incorporated.

**One suggestion I did not follow:** I chose not to tune the model or add extra features to improve its accuracy. I retained the exact `LogisticRegression` configuration, six required features, and train/test split specified in the assignment. This avoided changing the required implementation to improve a metric that was not being evaluated.

## What I'd Improve

If the application needed to support significantly larger workloads, I would consider:

- **Large-scale historical imports:** For 10 million history rows, replace the in-memory `seen` set with a staging table, unique index, `INSERT ... ON CONFLICT DO NOTHING`, and PostgreSQL `COPY`.
- **Database scalability:** Move from SQLite to PostgreSQL and consider monthly partitioning of `HistoricalOrder`.
- **Reporting performance:** Retain appropriate indexes and introduce daily pre-aggregated rollups if reporting volume grows.
- **Pagination:** Use keyset pagination based on `(created_at, order_id)` instead of offset pagination for large datasets.
- **Model training:** Stream feature generation, calculate `prior_orders` using a SQL window function, and run training as a separate job.
- **Model management:** Store versioned model artifacts in object storage with a registry pointer.
- **Outbox processing:** Add a background relay worker to process outbox messages.
- **Concurrency testing:** Add PostgreSQL-backed concurrency and load tests.
- **API security and documentation:** Add authentication, rate limiting, and an OpenAPI schema.
