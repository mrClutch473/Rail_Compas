import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_home_page(client):
    response = client.get(reverse("web:home"))
    assert response.status_code == 200
    assert "ЖД" in response.content.decode()


@pytest.mark.django_db
def test_healthz_reports_db(client):
    response = client.get(reverse("web:healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}


def test_admin_login_page(client, settings):
    response = client.get(f"/{settings.ADMIN_URL}login/")
    assert response.status_code == 200
