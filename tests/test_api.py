import unittest
from dataclasses import dataclass
from unittest.mock import patch

from fastapi.testclient import TestClient

from sowa_mobi.api import create_app


@dataclass
class FakeLoan:
    short_title: str
    due_date: str
    branch: str = ""
    copy_id: str = "copy-1"
    can_prolong: bool = True


@dataclass
class FakeReservation:
    title: str
    status: str = "oczekuje"
    reservation_id: str = "reservation-1"


class FakeClient:
    def __init__(self, account):
        self.account = account
        self.logged_in = True
        self.prolonged = []
        self.reserved = []
        self.cancelled = []

    def get_loans(self):
        return [FakeLoan("Książka testowa", "01.01.2030", "Kórnik")]

    def get_reservations(self):
        return [FakeReservation("Rezerwacja testowa")]

    def get_account_info(self):
        return type("Info", (), {"name": self.account, "email": "", "debt": "0,00 PLN"})()

    def prolong_by_copy_id(self, copy_id):
        self.prolonged.append(copy_id)
        return True, "Prolongata wykonana"

    def reserve(self, idw, agenda, pickup, csrf_token):
        self.reserved.append((idw, agenda, pickup))
        return True, "Rezerwacja wykonana"

    def cancel_reservation(self, reservation_id):
        self.cancelled.append(reservation_id)
        return True, "Rezerwacja anulowana"

    def search_catalog(self, query):
        return [{
            "record_id": "R1",
            "title": query,
            "options": [{
                "idw": "U123",
                "agenda": "03",
                "pickup": "03",
                "csrf_token": "csrf-internal",
                "action": "order",
            }],
        }]


class ApiTests(unittest.TestCase):
    def setUp(self):
        config = {
            "base_url": "https://example.invalid",
            "kat_id": 519,
            "accounts": {
                "wiktor": {"email": "w@example.invalid", "password": "secret", "api_token": "token-w"},
                "zona": {"email": "z@example.invalid", "password": "secret", "api_token": "token-z"},
            },
        }
        self.clients = {}

        def factory(account):
            self.clients[account] = FakeClient(account)
            return self.clients[account]

        self.client = TestClient(create_app(config, client_factory=factory))
        self.headers = {"Authorization": "Bearer token-w"}

    def test_health_is_public(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIsInstance(response.json()["hint"], list)

    def test_missing_or_invalid_token_is_rejected(self):
        response = self.client.get("/v1/loans")
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.json()["hint"])
        self.assertIn("Bearer", response.json()["hint"][0])
        self.assertEqual(
            self.client.get("/v1/loans", headers={"Authorization": "Bearer wrong"}).status_code,
            401,
        )

    def test_account_can_override_library_configuration(self):
        config = {
            "base_url": "https://default.example",
            "kat_id": 1,
            "accounts": {
                "wiktor": {
                    "email": "reader@example.org",
                    "password": "password",
                    "api_token": "token-w",
                    "base_url": "https://account-library.example",
                    "kat_id": 42,
                }
            },
        }
        with patch("sowa_mobi.api.SowaOPAC") as client_class:
            client_class.return_value.logged_in = True
            client_class.return_value.get_loans.return_value = []
            app = create_app(config)
            response = TestClient(app).get("/v1/loans", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        client_class.assert_called_once_with("https://account-library.example", 42)

    def test_token_selects_only_its_account(self):
        response = self.client.get("/v1/loans", headers={"Authorization": "Bearer token-z"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account"], "zona")
        self.assertEqual(response.json()["loans"][0]["title"], "Książka testowa")

    def test_account_identity_does_not_expose_credentials(self):
        response = self.client.get("/v1/account", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account"], "wiktor")
        self.assertNotIn("password", response.text)
        self.assertNotIn("email", response.json())

    def test_prolongation_is_exposed_for_a_specific_copy(self):
        response = self.client.post("/v1/loans/copy-1/prolong", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["success"], True)
        self.assertEqual(self.clients["wiktor"].prolonged, ["copy-1"])

    def test_catalog_search_is_account_scoped(self):
        response = self.client.get("/v1/catalog/search?q=argumentacja", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["title"], "argumentacja")
        option = response.json()["results"][0]["options"][0]
        self.assertIn("option_id", option)
        self.assertNotIn("csrf_token", option)
        self.assertNotIn("idw", option)
        self.assertTrue(any("Use option_id" in hint for hint in response.json()["hint"]))

    def test_openapi_is_available_and_documents_hints(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/v1/catalog/search", response.json()["paths"])
        schema = response.json()["components"]["schemas"]["HintedResponse"]
        self.assertIn("hint", schema["properties"])

    def test_reservation_and_cancellation_are_exposed(self):
        self.client.get("/v1/catalog/search?q=argumentacja", headers=self.headers)
        response = self.client.post(
            "/v1/reservations",
            headers=self.headers,
            json={"option_id": "R1:0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.clients["wiktor"].reserved, [("U123", "03", "03")])

        response = self.client.delete("/v1/reservations/reservation-1", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.clients["wiktor"].cancelled, ["reservation-1"])


if __name__ == "__main__":
    unittest.main()
