"""Thin HTTP layer: parse input, call a service/query, shape the JSON response."""
from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .errors import InvalidInput, MissingFields
from .history import PAGE_SIZE, filter_history, paginate, sku_stats
from .ml.predict import predict_return_risk
from .models import HistoricalOrder, Item, Order, OutboxMessage
from .services.purchases import record_purchase
from .validation import normalise_optional, normalise_token, parse_query_date


def _body(request: Request) -> dict:
    data = request.data
    if not isinstance(data, dict):
        raise MissingFields()
    return data


def _required_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MissingFields()
    return value


def _optional_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInput("invalid_" + key)
    return value


def order_payload(order: Order) -> dict:
    return {
        "id": order.pk,
        "email": order.customer.email,
        "sku": order.item.sku,
        "extraCode": order.extra.code if order.extra else None,
        "externalChargeId": order.external_charge_id,
        "totalCents": order.total_cents,
    }


class PurchaseView(APIView):
    def post(self, request: Request) -> Response:
        data = _body(request)
        order = record_purchase(
            email=_required_str(data, "email"),
            sku=_required_str(data, "sku"),
            external_charge_id=_required_str(data, "externalChargeId"),
            extra_code=_optional_str(data, "extraCode"),
        )
        return Response(
            {"replayed": order.replayed, "order": order_payload(order)},
            status=200 if order.replayed else 201,
        )


class CatalogView(APIView):
    def get(self, request: Request, sku: str) -> Response:
        item = Item.objects.filter(sku=normalise_token(sku)).first()
        if item is None:
            raise NotFound()
        return Response(
            {"sku": item.sku, "name": item.name, "priceCents": item.price_cents, "remaining": item.remaining}
        )


class OutboxView(APIView):
    def get(self, request: Request) -> Response:
        rows = OutboxMessage.objects.order_by("id").values("to_email", "kind", "external_charge_id")
        return Response(
            [{"toEmail": r["to_email"], "kind": r["kind"], "externalChargeId": r["external_charge_id"]} for r in rows]
        )


def _date_param(request: Request, name: str):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        return parse_query_date(raw)
    except ValueError:
        raise InvalidInput("invalid_date") from None


def _date_range(request: Request):
    return _date_param(request, "from"), _date_param(request, "to")


class HistoryView(APIView):
    def get(self, request: Request) -> Response:
        date_from, date_to = _date_range(request)
        raw_page = request.query_params.get("page", "1")
        if not raw_page.isascii() or not raw_page.isdigit() or int(raw_page) < 1:
            raise InvalidInput("invalid_page")
        page = int(raw_page)
        qs = filter_history(
            sku=normalise_optional(request.query_params.get("sku")),
            channel=normalise_optional(request.query_params.get("channel")),
            date_from=date_from,
            date_to=date_to,
        )
        count, rows = paginate(qs, page)
        return Response({"count": count, "page": page, "pageSize": PAGE_SIZE, "results": [_history_payload(r) for r in rows]})


def _history_payload(row: HistoricalOrder) -> dict:
    return {
        "orderId": row.order_id,
        "email": row.email,
        "sku": row.sku,
        "extraCode": row.extra_code,
        "channel": row.channel,
        "createdAt": row.created_at.isoformat().replace("+00:00", "Z"),
        "totalCents": row.total_cents,
        "returned": row.returned,
    }


class HistoryStatsView(APIView):
    def get(self, request: Request) -> Response:
        date_from, date_to = _date_range(request)
        return Response(sku_stats(date_from, date_to))


class ReturnRiskView(APIView):
    def post(self, request: Request) -> Response:
        data = _body(request)
        probability, version = predict_return_risk(
            email=_required_str(data, "email"),
            sku=_required_str(data, "sku"),
            extra_code=_optional_str(data, "extraCode"),
            channel=_required_str(data, "channel"),
            created_at=_required_str(data, "createdAt"),
        )
        return Response({"probability": probability, "modelVersion": version})
