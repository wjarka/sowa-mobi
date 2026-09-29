import unittest
from unittest.mock import patch

from scripts.update_homelab import update_compose
from scripts.verify_deployment import main as verify_deployment


class ImagePromotionTests(unittest.TestCase):
    digest = "sha256:" + "a" * 64
    revision = "b" * 40
    suffix = '    environment:\n      SOWA_CONFIG_JSON: ${SOWA_CONFIG_JSON}\n'

    def test_migrates_build_without_touching_runtime_settings(self):
        compose = (
            'services:\n  sowa-mobi:\n    build:\n'
            '      context: "https://github.com/wjarka/sowa-mobi.git#' + "c" * 40 + '"\n'
            '      dockerfile: Dockerfile\n' + self.suffix
        )
        updated = update_compose(compose, self.digest, self.revision)
        self.assertNotIn("build:", updated)
        self.assertIn(f"image: ghcr.io/wjarka/sowa-mobi@{self.digest}", updated)
        self.assertTrue(updated.endswith(self.suffix))
        self.assertEqual(update_compose(updated, self.digest, self.revision), updated)

    def test_updates_digest_and_source_together(self):
        compose = (
            'services:\n  sowa-mobi:\n'
            '    # Source: https://github.com/wjarka/sowa-mobi/commit/' + "c" * 40 + '\n'
            '    image: ghcr.io/wjarka/sowa-mobi@sha256:' + "d" * 64 + '\n' + self.suffix
        )
        updated = update_compose(compose, self.digest, self.revision)
        self.assertIn(self.revision, updated)
        self.assertIn(self.digest, updated)
        self.assertNotIn("d" * 64, updated)
        self.assertTrue(updated.endswith(self.suffix))

    def test_rejects_unknown_layout_or_untrusted_digest(self):
        with self.assertRaises(ValueError):
            update_compose("services: {}\n", self.digest, self.revision)
        with self.assertRaises(ValueError):
            update_compose("services: {}\n", "latest\n    privileged: true", self.revision)
        with self.assertRaises(ValueError):
            update_compose("services: {}\n", self.digest, "master")


class DeploymentReceiptTests(unittest.TestCase):
    def receipt(self, revision="b" * 40, digest="sha256:" + "a" * 64):
        return {"status": "healthy", "revision": revision,
                "image": f"ghcr.io/wjarka/sowa-mobi@{digest}"}

    def verify(self):
        with patch("sys.argv", ["verify", "homelab", "b" * 40, "sha256:" + "a" * 64]):
            verify_deployment()

    @patch("scripts.verify_deployment.time.sleep")
    @patch("scripts.verify_deployment.read_receipt")
    def test_waits_for_correct_revision_and_digest(self, read, sleep):
        read.side_effect = [self.receipt(revision="c" * 40),
                            self.receipt(digest="sha256:" + "d" * 64), self.receipt()]
        self.verify()
        self.assertEqual(read.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("scripts.verify_deployment.time.sleep")
    @patch("scripts.verify_deployment.read_receipt")
    def test_unhealthy_receipt_is_not_success(self, read, sleep):
        unhealthy = self.receipt() | {"status": "unhealthy"}
        read.side_effect = [unhealthy, self.receipt()]
        self.verify()
        sleep.assert_called_once()

    @patch("scripts.verify_deployment.time.monotonic", side_effect=[0, 601])
    def test_times_out_instead_of_reporting_success(self, clock):
        with self.assertRaisesRegex(SystemExit, "No matching healthy deployment receipt"):
            self.verify()
