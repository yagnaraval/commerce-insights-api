"""Domain exception hierarchy. Each error carries a stable machine code and an HTTP status."""


class DomainError(Exception):
    """Base class for expected, client-caused failures."""

    code = "error"
    http_status = 400

    def __init__(self, code: str | None = None) -> None:
        self.code = code or self.code
        super().__init__(self.code)


class InvalidChargeId(DomainError):
    code = "invalid_charge_id"


class UnknownItem(DomainError):
    code = "unknown_item"


class ExtraNotAllowed(DomainError):
    code = "extra_not_allowed"


class MissingFields(DomainError):
    code = "missing_fields"


class InvalidInput(DomainError):
    """Generic validation failure; the code names the offending field/rule."""


class SoldOut(DomainError):
    code = "sold_out"
    http_status = 409


class DuplicatePurchase(DomainError):
    code = "duplicate_purchase"
    http_status = 409


class ModelNotTrained(DomainError):
    code = "model_not_trained"
    http_status = 503
