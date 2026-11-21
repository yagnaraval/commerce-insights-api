from django.urls import re_path

from . import views

# Every route accepts an optional trailing slash (the brief mixes both styles).
urlpatterns = [
    re_path(r"^purchases/?$", views.PurchaseView.as_view()),
    re_path(r"^catalog/(?P<sku>[^/]+)/?$", views.CatalogView.as_view()),
    re_path(r"^outbox/?$", views.OutboxView.as_view()),
    re_path(r"^history/stats/?$", views.HistoryStatsView.as_view()),
    re_path(r"^history/?$", views.HistoryView.as_view()),
    re_path(r"^predictions/return-risk/?$", views.ReturnRiskView.as_view()),
]
