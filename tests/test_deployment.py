import unittest

from scripts.update_homelab import update_compose


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
