import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sowa_mobi.api import create_app
from sowa_mobi.sowa_opac import SowaOPAC

LOGIN = '<form name="log"><input name="swww_user"></form>'
LOANS = '<div id="results-panel"><div class="record-details"><div class="record-meta">Example</div></div></div>'

class SessionExpiryTests(unittest.TestCase):
    def setUp(self):
        self.client = SowaOPAC('https://example.invalid', 0)
        self.client.logged_in = True
        def login(*args):
            self.client.logged_in = True
            return True
        self.client.login = Mock(side_effect=login)
        config = {'accounts': {'test': {'email': 'x', 'password': 'y', 'api_token': 'token'}}}
        self.api = TestClient(create_app(config, client_factory=lambda _: self.client))
        self.headers = {'Authorization': 'Bearer token'}

    def test_expired_session_relogs_and_retries_read(self):
        self.client._get = Mock(side_effect=[Mock(text=LOGIN), Mock(text=LOANS)])
        response = self.api.get('/v1/loans', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['loans']), 1)
        self.client.login.assert_called_once_with('x', 'y')

    def test_failed_relogin_is_502_not_empty_success(self):
        self.client._get = Mock(return_value=Mock(text=LOGIN))
        self.client.login.side_effect = None
        self.client.login.return_value = False
        response = self.api.get('/v1/loans', headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertIn('hint', response.json())

    def test_still_expired_after_login_is_502(self):
        self.client._get = Mock(return_value=Mock(text=LOGIN))
        response = self.api.get('/v1/reservations', headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.client.login.assert_called_once()

if __name__ == '__main__':
    unittest.main()
