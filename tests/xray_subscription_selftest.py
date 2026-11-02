import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest

from xray_selftest import XrayTests, PANEL, ROOT, xray

loader = importlib.machinery.SourceFileLoader("xray_test_sub", os.path.join(ROOT, "swg-sub"))
spec = importlib.util.spec_from_loader(loader.name, loader)
SUB = importlib.util.module_from_spec(spec)
loader.exec_module(SUB)


class SubscriptionTests(XrayTests):
    def test_subscription_without_wireguard_blob(self):
        self.create()
        user = self.api("/api/users/create", {"name": "Xray subscription"})[1]["data"]
        peer = self.peer()
        self.assertEqual(self.api("/api/peers/update", {"peer_id": peer["id"], "user_id": user["id"]})[0], 200)
        self.assertEqual(self.api("/api/peers/add-target", {"peer_id": peer["id"], "target": {"node": self.node_id, "iface": "xray1"}})[0], 409)
        token = "isolated-test-token"
        sub_dir = os.path.join(self.directory.name, "subs")
        xray._write(os.path.join(sub_dir, "serve.json"), {"enabled": True, "turn_enabled": False})
        xray._write(os.path.join(sub_dir, "users.json"), {user["id"]: {"enabled": True, "token_sha": hashlib.sha256(token.encode()).hexdigest()}})
        state = SUB.State(dict(self.deps, sub_dir=sub_dir, stats_dir=self.directory.name))
        SUB._READ_CACHE.update(at=-1e9, roster=None, describe=None, users=None, nodes=None)
        data = SUB.read_data(state, token)
        self.assertFalse(data["turn_enabled"])
        self.assertIsNone(data["peers"][0]["sec"])
        target = data["peers"][0]["targets"][0]
        self.assertEqual(target["type"], "xray")
        self.assertTrue(target["xray"]["uri"].startswith("vless://"))
        self.assertNotIn("private_key", json.dumps(data))
        self.assertEqual(self.api("/api/peers/update", {"peer_id": peer["id"], "sub_hide": ["xray"]})[0], 400)
        self.api("/api/user/block", {"user_id": user["id"]})
        SUB._READ_CACHE.update(at=-1e9, roster=None)
        self.assertTrue(SUB.read_data(state, token)["disabled"])


if __name__ == "__main__":
    unittest.main()
