import unittest
from unittest.mock import Mock

from sowa_mobi.sowa_opac import SowaOPAC


class LoginEndpointTests(unittest.TestCase):
    def test_login_requests_current_sowa_login_page(self):
        client = SowaOPAC("https://example.invalid", kat_id=519)

        login_page = Mock(
            text='<input name="KatID" value="0"><input name="fwrqpid" value="csrf123">'
        )
        login_result = Mock(text='<main>authenticated</main>')
        calls = []

        def fake_get(path, **kwargs):
            calls.append(("GET", path))
            return login_page

        def fake_post(path, **kwargs):
            calls.append(("POST", path))
            return login_result

        client._get = fake_get
        client._post = fake_post

        self.assertTrue(client.login("reader@example.invalid", "password"))
        self.assertEqual(calls[0], ("GET", "index.php?typ=acc&id=login"))


if __name__ == "__main__":
    unittest.main()
