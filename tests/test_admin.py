import pytest
from django.contrib import admin
from django.urls import reverse

from .factories import PriceSnapshotFactory, StationFactory

pytestmark = pytest.mark.django_db

PROJECT_MODELS = [model for model in admin.site._registry if model._meta.app_label not in ("auth",)]


@pytest.mark.parametrize("model", PROJECT_MODELS, ids=lambda m: m._meta.label)
def test_admin_changelist_opens(admin_client, model):
    PriceSnapshotFactory()
    StationFactory()
    url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
    assert admin_client.get(url).status_code == 200


def test_snapshots_are_read_only(admin_client):
    snapshot = PriceSnapshotFactory()
    add_url = reverse("admin:offers_pricesnapshot_add")
    assert admin_client.get(add_url).status_code == 403
    change_url = reverse("admin:offers_pricesnapshot_change", args=[snapshot.pk])
    response = admin_client.post(change_url, {"price": "1"})
    assert response.status_code == 403
