import copy
import json
from pathlib import Path
import tempfile
import unittest

import trust


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.policy = {"default": [{"type": "insecureAcceptAnything"}], "transports": {
            "docker": {trust.OFFICIAL: [{"type": "sigstoreSigned", "keyPath": "/official.pub",
                                       "signedIdentity": {"type": "matchRepository"}}]}}}

    def test_official_trust_preserved_owner_scoped_and_default_rejects(self):
        original = copy.deepcopy(self.policy)
        result = trust.policy_with_owner(self.policy)
        self.assertEqual(self.policy, original)
        self.assertEqual(result["transports"]["docker"][trust.OFFICIAL],
                         original["transports"]["docker"][trust.OFFICIAL])
        self.assertEqual(result["default"], [{"type": "reject"}])
        self.assertEqual(result["transports"]["docker"][trust.REPOSITORY][0]["type"], "signedBy")

    def test_missing_official_or_conflicting_scope_rejected(self):
        for scope in (trust.REPOSITORY, trust.REPOSITORY + ":latest",
                      trust.REPOSITORY + "/sub", "localhost:30070"):
            policy = copy.deepcopy(self.policy)
            policy["transports"]["docker"][scope] = [{"type": "reject"}]
            with self.assertRaises(ValueError):
                trust.policy_with_owner(policy)
        self.policy["transports"]["docker"].clear()
        with self.assertRaises(ValueError):
            trust.policy_with_owner(self.policy)

    def test_unsigned_exceptions_rejected(self):
        self.policy["transports"]["docker"]["example.org"] = [{"type": "insecureAcceptAnything"}]
        with self.assertRaises(ValueError):
            trust.policy_with_owner(self.policy)

    def test_fedora_local_daemon_exception_is_tightened(self):
        self.policy["transports"]["docker-daemon"] = {"": [{"type": "insecureAcceptAnything"}]}
        result = trust.policy_with_owner(self.policy)
        self.assertEqual(result["transports"]["docker-daemon"], {"": [{"type": "reject"}]})

    def test_publication_does_not_follow_symlinks_or_overwrite_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            trust.publish(p / "public", b"original")
            with self.assertRaises(FileExistsError):
                trust.publish(p / "public", b"replacement")
            (p / "link").symlink_to(p / "public")
            with self.assertRaises(ValueError):
                trust.publish(p / "link", b"replacement", replace=True)
            self.assertEqual((p / "public").read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
