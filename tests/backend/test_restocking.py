"""
Tests for restocking order API endpoints.
"""
from datetime import datetime, timedelta

import pytest

import main


@pytest.fixture(autouse=True)
def clear_restocking_orders():
    """Reset the in-memory restocking order store so tests stay independent."""
    main.restocking_orders.clear()
    yield
    main.restocking_orders.clear()


class TestRestockingEndpoints:
    """Test suite for restocking-related endpoints."""

    def test_demand_forecasts_include_cost_and_lead_time(self, client):
        """Test that demand forecasts expose unit_cost and lead_time_days."""
        response = client.get("/api/demand")
        assert response.status_code == 200

        data = response.json()
        assert len(data) > 0
        for forecast in data:
            assert isinstance(forecast["unit_cost"], (int, float))
            assert isinstance(forecast["lead_time_days"], int)
            assert forecast["unit_cost"] > 0
            assert forecast["lead_time_days"] > 0

    def test_get_restocking_orders_empty(self, client):
        """Test that no restocking orders exist before any are submitted."""
        response = client.get("/api/restocking-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restocking_order(self, client):
        """Test submitting a valid restocking order."""
        response = client.post("/api/restocking-orders", json={
            "budget": 10000,
            "items": [
                {"sku": "WDG-001", "quantity": 100},
                {"sku": "GSK-203", "quantity": 50}
            ]
        })
        assert response.status_code == 201

        order = response.json()
        assert order["status"] == "Submitted"
        assert order["order_number"].startswith("RST-")
        assert len(order["items"]) == 2
        for item in order["items"]:
            assert "name" in item
            assert "line_total" in item

    def test_restocking_order_total_calculation(self, client):
        """Test that totals are computed from server-side unit costs."""
        forecasts = {f["item_sku"]: f for f in client.get("/api/demand").json()}
        response = client.post("/api/restocking-orders", json={
            "budget": 10000,
            "items": [
                {"sku": "WDG-001", "quantity": 100},
                {"sku": "GSK-203", "quantity": 50}
            ]
        })
        order = response.json()

        expected_total = (100 * forecasts["WDG-001"]["unit_cost"]
                          + 50 * forecasts["GSK-203"]["unit_cost"])
        assert abs(order["total_cost"] - expected_total) < 0.01

    def test_restocking_order_lead_time_calculation(self, client):
        """Test that order lead time is the slowest item and drives expected delivery."""
        forecasts = {f["item_sku"]: f for f in client.get("/api/demand").json()}
        response = client.post("/api/restocking-orders", json={
            "budget": 100000,
            "items": [
                {"sku": "GSK-203", "quantity": 10},
                {"sku": "CTL-330", "quantity": 1}
            ]
        })
        order = response.json()

        expected_lead = max(forecasts["GSK-203"]["lead_time_days"],
                            forecasts["CTL-330"]["lead_time_days"])
        assert order["lead_time_days"] == expected_lead

        created = datetime.fromisoformat(order["created_date"])
        expected_delivery = datetime.fromisoformat(order["expected_delivery"])
        assert expected_delivery - created == timedelta(days=expected_lead)

    def test_submitted_order_appears_in_list(self, client):
        """Test that submitted orders are listed, newest first."""
        first = client.post("/api/restocking-orders", json={
            "budget": 1000, "items": [{"sku": "GSK-203", "quantity": 10}]
        }).json()
        second = client.post("/api/restocking-orders", json={
            "budget": 1000, "items": [{"sku": "FLT-405", "quantity": 10}]
        }).json()

        response = client.get("/api/restocking-orders")
        assert response.status_code == 200

        data = response.json()
        assert [o["id"] for o in data] == [second["id"], first["id"]]

    def test_create_restocking_order_unknown_sku(self, client):
        """Test that an unknown SKU is rejected."""
        response = client.post("/api/restocking-orders", json={
            "budget": 1000, "items": [{"sku": "NOPE-999", "quantity": 1}]
        })
        assert response.status_code == 400
        assert "unknown sku" in response.json()["detail"].lower()

    def test_create_restocking_order_over_budget(self, client):
        """Test that an order exceeding the budget is rejected and not stored."""
        response = client.post("/api/restocking-orders", json={
            "budget": 10, "items": [{"sku": "CTL-330", "quantity": 1}]
        })
        assert response.status_code == 400
        assert "exceeds budget" in response.json()["detail"].lower()
        assert client.get("/api/restocking-orders").json() == []

    @pytest.mark.parametrize("quantity", [0, -5])
    def test_create_restocking_order_non_positive_quantity(self, client, quantity):
        """Test that zero or negative quantities are rejected."""
        response = client.post("/api/restocking-orders", json={
            "budget": 1000, "items": [{"sku": "GSK-203", "quantity": quantity}]
        })
        assert response.status_code == 400

    def test_create_restocking_order_empty_items(self, client):
        """Test that an order with no items is rejected."""
        response = client.post("/api/restocking-orders", json={"budget": 1000, "items": []})
        assert response.status_code == 400

    def test_create_restocking_order_duplicate_sku(self, client):
        """Test that the same SKU twice in one order is rejected."""
        response = client.post("/api/restocking-orders", json={
            "budget": 1000,
            "items": [{"sku": "GSK-203", "quantity": 5}, {"sku": "GSK-203", "quantity": 5}]
        })
        assert response.status_code == 400
        assert "duplicate sku" in response.json()["detail"].lower()

    def test_create_restocking_order_decreasing_demand_sku(self, client):
        """Test that an item with falling forecast demand can't be restocked."""
        response = client.post("/api/restocking-orders", json={
            "budget": 100000, "items": [{"sku": "MTR-304", "quantity": 1}]
        })
        assert response.status_code == 400
        assert "demand gap" in response.json()["detail"].lower()

    def test_create_restocking_order_quantity_over_gap(self, client):
        """Test that quantities above the forecast demand gap are rejected."""
        forecast = next(f for f in client.get("/api/demand").json() if f["item_sku"] == "GSK-203")
        gap = forecast["forecasted_demand"] - forecast["current_demand"]
        response = client.post("/api/restocking-orders", json={
            "budget": 100000, "items": [{"sku": "GSK-203", "quantity": gap + 1}]
        })
        assert response.status_code == 400

    def test_create_restocking_order_exactly_at_budget(self, client):
        """Test that an order costing exactly the budget is accepted."""
        forecast = next(f for f in client.get("/api/demand").json() if f["item_sku"] == "GSK-203")
        response = client.post("/api/restocking-orders", json={
            "budget": forecast["unit_cost"] * 10, "items": [{"sku": "GSK-203", "quantity": 10}]
        })
        assert response.status_code == 201

    def test_create_restocking_order_missing_fields(self, client):
        """Test that a malformed request fails Pydantic validation."""
        response = client.post("/api/restocking-orders", json={"items": []})
        assert response.status_code == 422
