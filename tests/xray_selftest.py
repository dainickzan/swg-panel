import base64
import copy
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import xray_support as xray


def load_panel():
    loader = importlib.machinery.SourceFileLoader("xray_test_panel", os.path.join(ROOT, "swg-panel-server"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


PANEL = load_panel()


class XrayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.node_id = "test-node"
        self.nodes = {self.node_id: {"name": "test", "endpoint_host": "203.0.113.10", "xray": {}}}
        self.deps = {"fleet": {}, "roster_path": os.path.join(self.directory.name, "users.json"),
                     "nodes_path": os.path.join(self.directory.name, "nodes.json"), "panel_settings": {},
                     "node_snaps": {self.node_id: {"xray_capable": True}}, "node_seen": {}}
        PANEL.nodes_save(self.deps["nodes_path"], self.nodes)
        PANEL.roster_save(self.deps["roster_path"], {"version": PANEL.ROSTER_VERSION, "users": {}, "peers": {}})

    def api(self, path, body=None, method="POST", query=None):
        return PANEL.api(method, path, query or {}, body or {}, self.deps)

    def create(self, iface="xray1", **extra):
        status, response = self.api("/api/xray/set", dict(node=self.node_id, iface=iface, **extra))
        self.assertEqual(status, 200, response)
        return response["data"]

    def peer(self, iface="xray1"):
        status, response = self.api("/api/peers/create", {"title": "test", "targets": [{"node": self.node_id, "iface": iface}]})
        self.assertEqual(status, 200, response)
        return response["data"]

    def desired(self):
        return xray.desired(PANEL.roster_load(self.deps["roster_path"]), PANEL.nodes_load(self.deps["nodes_path"]), self.node_id,
                            keys=xray._read(os.path.join(self.directory.name, "xray-keys.json")))

    def test_create_and_secrets(self):
        record = self.create()
        self.assertEqual(record["security"], "reality")
        self.assertNotIn("private_key", record)
        self.assertNotIn("private_key", PANEL.nodes_load(self.deps["nodes_path"])[self.node_id]["xray"]["xray1"])
        private = self.desired()["xray1"]["private_key"]
        self.create(title="new")
        self.assertEqual(self.desired()["xray1"]["private_key"], private)
        self.assertEqual(len(base64.urlsafe_b64decode(private + "=")), 32)

    def test_peer_shared_lifecycle(self):
        self.create()
        peer = self.peer()
        self.assertEqual(peer["targets"][0]["type"], "xray")
        self.assertFalse(peer["pubkey"])
        self.assertEqual(len(self.desired()["xray1"]["clients"]), 1)
        self.api("/api/peers/block", {"peer_id": peer["id"]})
        self.assertEqual(self.desired()["xray1"]["clients"], [])
        self.api("/api/peers/unblock", {"peer_id": peer["id"]})
        self.assertEqual(len(self.desired()["xray1"]["clients"]), 1)
        self.api("/api/peers/update", {"peer_id": peer["id"], "title": "edited", "expiry": 1})
        self.assertEqual(self.desired()["xray1"]["clients"], [])
        self.api("/api/peers/update", {"peer_id": peer["id"], "expiry": 0})
        self.assertEqual(self.desired()["xray1"]["clients"][0]["id"], peer["xray_uuid"])

    def test_owner_expiry_and_block(self):
        self.create()
        peer = self.peer()
        roster = PANEL.roster_load(self.deps["roster_path"])
        roster["users"]["owner"] = {"name": "owner", "disabled": True}
        roster["peers"][peer["id"]]["user_id"] = "owner"
        nodes = PANEL.nodes_load(self.deps["nodes_path"])
        self.assertEqual(xray.desired(roster, nodes, self.node_id)["xray1"]["clients"], [])
        roster["users"]["owner"] = {"name": "owner", "expiry": 10}
        self.assertEqual(xray.desired(roster, nodes, self.node_id, now=11)["xray1"]["clients"], [])

    def test_multiple_deployments_and_rotate(self):
        self.create()
        self.create("xray2", port=10444)
        peer = self.peer()
        status, response = self.api("/api/peers/add-target", {"peer_id": peer["id"], "target": {"node": self.node_id, "iface": "xray2"}})
        self.assertEqual(status, 200, response)
        status, response = self.api("/api/xray-peer/rotate", {"peer_id": peer["id"]})
        self.assertEqual(status, 200, response)
        self.assertNotEqual(response["data"]["xray_uuid"], peer["xray_uuid"])
        desired = self.desired()
        self.assertEqual(desired["xray1"]["clients"], desired["xray2"]["clients"])
        self.api("/api/xray/delete", {"node": self.node_id, "iface": "xray1"})
        roster = PANEL.roster_load(self.deps["roster_path"])
        self.assertEqual(len(roster["peers"][peer["id"]]["targets"]), 1)
        self.assertIn("xray2", self.desired())
        self.assertIn("xray1", PANEL.nodes_load(self.deps["nodes_path"])[self.node_id]["xray_delete"])
        keys = xray._read(os.path.join(self.directory.name, "xray-keys.json"))
        self.assertNotIn(self.node_id + "/xray1", keys)

    def test_validation_and_port_conflicts(self):
        self.create()
        for body in ({"iface": "xray2"}, {"iface": "../xray3"}, {"iface": "xray3", "port": 70000},
                     {"iface": "xray3", "protocol": "trojan"}, {"iface": "xray3", "blocked_ips": ["bad"]},
                     {"iface": "xray3", "short_id": "1"}, {"iface": "xray3", "security": "none"},
                     {"iface": "xray3", "listen": "$(touch /tmp/no)"}):
            status, response = self.api("/api/xray/set", dict(node=self.node_id, **body))
            self.assertIn(status, (400, 409), response)
        self.assertEqual(list(self.desired()), ["xray1"])

    def test_actions_and_client_link(self):
        self.create()
        peer = self.peer()
        for action in ("stop", "start", "restart"):
            status, response = self.api("/api/xray/action", {"node": self.node_id, "iface": "xray1", "action": action})
            self.assertEqual(status, 200, response)
        status, response = self.api("/api/xray/config", method="GET", query={"peer_id": [peer["id"]], "node": [self.node_id], "iface": ["xray1"]})
        self.assertEqual(status, 200)
        self.assertTrue(response["data"]["uri"].startswith("vless://"))
        self.assertIn("pbk=", response["data"]["uri"])
        self.assertNotIn(self.desired()["xray1"]["private_key"], response["data"]["uri"])

    def test_other_protocols_and_routing(self):
        for protocol in ("vless", "vmess", "trojan", "shadowsocks"):
            body = {"protocol": protocol, "security": "none" if protocol == "shadowsocks" else "tls", "flow": "",
                    "certificate": "/var/lib/swg-noded/xray/tls/fullchain.pem", "certificate_key": "/var/lib/swg-noded/xray/tls/key.pem",
                    "blocked_domains": ["example.org"], "blocked_ips": ["10.1.2.3/8"], "egress_type": "socks", "egress_host": "127.0.0.1", "egress_port": 1080}
            record = xray.validate(body, {}, self.nodes[self.node_id])
            record.update(api_port=61001, clients=[{"id": xray.new_credential(), "email": "test@swg"}])
            config = xray.server_config(record)
            self.assertEqual(config["inbounds"][0]["protocol"], protocol)
            self.assertEqual(config["outbounds"][0]["protocol"], "socks")
            self.assertEqual(config["routing"]["rules"][-1]["ip"], ["10.0.0.0/8"])
            self.assertTrue(xray.client_uri(record, record["clients"][0]["id"]).startswith("ss://" if protocol == "shadowsocks" else protocol + "://"))

    def test_failed_sync_does_not_remove(self):
        with patch.object(xray, "capable", return_value=True), patch.object(xray, "_docker") as docker:
            xray.reconcile(None, ["xray1"], {"STATE_DIR": self.directory.name})
            docker.assert_not_called()

    def test_invalid_config_keeps_running_container(self):
        namespace = {"STATE_DIR": self.directory.name, "SWG_HOST_NODE_DIR": self.directory.name}
        record = self.create()
        commands = []
        def docker(unused_namespace, arguments, timeout=30):
            commands.append(arguments)
            return (1, "", "invalid configuration")
        with patch.object(xray, "capable", return_value=True), patch.object(xray, "_docker", side_effect=docker):
            xray.reconcile(self.desired(), [], namespace)
        self.assertFalse(any(command[:2] == ["rm", "-f"] for command in commands))

    def test_unmeasured_counters_do_not_double_count(self):
        directory = os.path.join(self.directory.name, "xray")
        state = {"xray1": {"api_port": 61001, "container_id": "server", "clients": [], "config": {}}}
        ledger = {"xray1/peer": {"container_id": "server", "rx_bytes": 100, "tx_bytes": 200, "uplink": 100, "downlink": 200, "at": 1}}
        xray._write(os.path.join(directory, "instances.json"), state)
        xray._write(os.path.join(directory, "traffic.json"), ledger)
        with patch.object(xray, "capable", return_value=True), patch.object(xray, "_running", return_value=True), patch.object(xray, "_docker", return_value=(0, '{"stat":[]}', "")):
            snapshot = xray.snapshot({"STATE_DIR": self.directory.name})[0]
        self.assertEqual(snapshot["rx_bytes"], 100)
        self.assertEqual(snapshot["tx_bytes"], 200)
        self.assertFalse(snapshot["peers"][0]["online"])


if __name__ == "__main__":
    unittest.main()
