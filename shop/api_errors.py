"""DRF exception handler mapping domain errors to ``{"error": code}`` responses."""
from rest_framework.exceptions import NotFound, ParseError
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .errors import DomainError


def exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response({"error": exc.code}, status=exc.http_status)
    if isinstance(exc, ParseError):
        return Response({"error": "invalid_json"}, status=400)
    if isinstance(exc, NotFound):
        return Response({"error": "not_found"}, status=404)
    return drf_exception_handler(exc, context)
