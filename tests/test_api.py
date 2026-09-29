import unittest
from dataclasses import dataclass
from unittest.mock import patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from sowa_mobi.api import create_app
from sowa_mobi.sowa_opac import SowaOPAC


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
    queue_pos: str = "2"
    expire_date: str = ""
    ready: bool = False
    pickup_by: str = ""


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
        return type("Info", (), {
            "name": self.account,
            "email": "",
            "debt": "Saldo konta: 0,00 PLN",
            "loans_count": 1,
            "reservations_count": 2,
            "loan_limit": None,
        })()

    def get_billing_summary(self):
        return {"balance": "Saldo konta: 0,00 PLN", "operations_count": 0, "fees": []}

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

    @patch.dict("os.environ", {"SOWA_REVISION": "abc123"})
    def test_health_identifies_the_running_release_without_logging_in(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.json()["revision"], "abc123")
        self.assertEqual(self.clients, {})

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
        self.assertEqual(response.json()["loans_count"], 1)
        self.assertEqual(response.json()["reservations_count"], 2)
        self.assertIsNone(response.json()["loan_limit"])
        self.assertNotIn("password", response.text)
        self.assertNotIn("email", response.json())

    def test_billing_endpoint_exposes_balance_and_operations(self):
        response = self.client.get("/v1/account/billing", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["balance"], "Saldo konta: 0,00 PLN")
        self.assertEqual(response.json()["operations_count"], 0)

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

    def test_reservation_parser_extracts_structured_status_and_cancel_id(self):
        html = """
        <div id="results-panel">
          <div class="record-details" data-recid="U105030">
            <div class="record-meta">Test title</div>
            <div class="record-info-meta">Jesteś na 1 miejscu w kolejce oczekujących na zwrot pozycji.</div>
            <form action="index.php?KatID=0&amp;typ=acc&amp;id=reserved" method="post">
              <input name="csrf_token" value="csrf-value">
              <input name="lendop" value="cancel-order">
              <input name="idw" value="U105030">
              <input name="agenda" value="03">
            </form>
          </div>
          <div class="record-details" data-recid="U104492">
            <div class="record-meta">Ready title</div>
            <div class="record-info-meta">Pozycja jest gotowa DO ODBIORU! Termin odbioru do 06.10.2026.</div>
          </div>
        </div>
        """
        client = SowaOPAC("https://example.invalid", 0)
        client.logged_in = True
        client._fetch_tab = lambda tab: BeautifulSoup(html, "html.parser")
        items = client.get_reservations()
        self.assertEqual(items[0].reservation_id, "U105030")
        self.assertEqual(items[0].queue_pos, "1")
        self.assertFalse(items[0].ready)
        self.assertEqual(items[1].reservation_id, "U104492")
        self.assertTrue(items[1].ready)
        self.assertEqual(items[1].expire_date, "06.10.2026")

    def test_reservation_and_cancellation_are_exposed(self):
        self.client.get("/v1/catalog/search?q=argumentacja", headers=self.headers)
        response = self.client.post(
            "/v1/reservations",
            headers=self.headers,
            json={"option_id": "R1:0"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.clients["wiktor"].reserved, [("U123", "03", "03")])

        listed = self.client.get("/v1/reservations", headers=self.headers).json()["reservations"][0]
        self.assertEqual(listed["reservation_id"], "reservation-1")
        self.assertEqual(listed["queue_pos"], "2")
        self.assertFalse(listed["ready"])

        response = self.client.delete("/v1/reservations/reservation-1", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.clients["wiktor"].cancelled, ["reservation-1"])


if __name__ == "__main__":
    unittest.main()
