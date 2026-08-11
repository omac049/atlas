from fastapi.testclient import TestClient

from apps.api.main import app


def test_health_is_paper_only():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["trading_enabled"] is False


def test_dashboard_and_overview_are_available():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    overview = client.get("/api/overview")
    assert overview.status_code == 200
    assert overview.json()["paper_only"] is True
    assert "evidence" in overview.json()
    assert overview.json()["evidence"]["mode"] == "LIVE_DISCOVERY_FIXTURE_EXECUTION"
    assert "shadow_observation" in overview.json()
    assert "training_readiness" in overview.json()
    assert "validation" in overview.json()
    assert "trusted_labels" in overview.json()["validation"]
