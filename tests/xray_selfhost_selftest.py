import copy
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xray_selftest as baseline
import xray_selfhost as selfhost


class SelfHostTests(baseline.XrayTests):
    def setUp(self):
        super().setUp()
        self.deps["node_snaps"][self.node_id].update(xray_selfhost_capable=True, xray_tcp_ports=[443, 8444])
        self.settings = dict(node=self.node_id, domain="decoy.example.org", email="admin@example.org", https_port=4443)
        self.dns = patch.object(selfhost, "resolve", return_value=["203.0.113.10"])
        self.dns.start()
        self.addCleanup(self.dns.stop)

    def setup_decoy(self):
        status, response = self.api("/api/xray/selfhost/setup", self.settings)
        self.assertEqual(status, 200, response)
        return response["data"]

    def ready(self):
        self.deps["node_snaps"][self.node_id]["xray_selfhost"] = dict(ready=True, domain=self.settings["domain"], https_port=4443)

    def test_domains_and_certificates(self):
        for domain in ("185.99.98.195", "6zxc66", "https://example.org", "../example.org", "x.local", "a.invalid", "a test.org", "a.example.org\nattack"):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                selfhost.domain_name(domain)
        self.assertEqual(selfhost.domain_name("Decoy.Example.Org."), "decoy.example.org")
        for port in (80, 443, 60000):
            with self.subTest(port=port), self.assertRaises(ValueError):
                selfhost.validate(dict(self.settings, https_port=port))
        for path in ("/etc/ssl/key.pem", "/var/lib/swg-noded/xray/tls/../key.pem", "/var/lib/swg-noded/xray/tls/key,pem"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                selfhost.validate(dict(self.settings, mode="certificate", certificate=path, certificate_key=path))
        record = selfhost.validate(dict(self.settings, mode="certificate", certificate="/var/lib/swg-noded/xray/tls/fullchain.pem", certificate_key="/var/lib/swg-noded/xray/tls/key.pem"))
        self.assertIsNone(record["http_port"])
        self.assertNotIn("email", record)

    def test_dns_check_needs_no_email_and_never_saves(self):
        before = baseline.PANEL.nodes_load(self.deps["nodes_path"])
        status, response = self.api("/api/xray/selfhost/check", dict(node=self.node_id, domain=self.settings["domain"]))
        self.assertEqual(status, 200, response)
        self.assertTrue(response["data"]["ok"])
        self.assertEqual(before, baseline.PANEL.nodes_load(self.deps["nodes_path"]))

    def test_dns_mismatch_and_lookup_failure(self):
        for resolved in (["203.0.113.10", "2001:db8::2"], ["198.51.100.1"], []):
            with self.subTest(resolved=resolved), patch.object(selfhost, "resolve", side_effect=[resolved, ["203.0.113.10"]]):
                status, response = self.api("/api/xray/selfhost/setup", self.settings)
                self.assertEqual(status, 400, response)
        with patch.object(selfhost, "resolve", side_effect=OSError("no DNS")):
            self.assertFalse(selfhost.dns_check(self.settings["domain"], "203.0.113.10")["ok"])
        self.assertNotIn("xray_selfhost", baseline.PANEL.nodes_load(self.deps["nodes_path"])[self.node_id])

    def test_occupied_ports_and_capability(self):
        for port in (80, 4443):
            self.deps["node_snaps"][self.node_id]["xray_tcp_ports"] = [port]
            status, response = self.api("/api/xray/selfhost/setup", self.settings)
            self.assertEqual(status, 400, response)
            self.assertIn("occupied", response["error"])
        self.deps["node_snaps"][self.node_id]["xray_selfhost_capable"] = False
        self.assertEqual(self.api("/api/xray/selfhost/check", self.settings)[0], 400)

    def test_ready_gate_preserves_keys_and_used_decoy(self):
        original = self.create()
        key_path = os.path.join(self.directory.name, "xray-keys.json")
        keys = baseline.xray._read(key_path)
        self.setup_decoy()
        self.assertEqual(self.api("/api/xray/set", dict(node=self.node_id, iface="xray1", selfhost=True))[0], 400)
        self.ready()
        record = self.create(selfhost=True)
        self.assertEqual(record["target"], "127.0.0.1:4443")
        self.assertEqual(record["server_name"], self.settings["domain"])
        self.assertEqual(record["public_key"], original["public_key"])
        self.assertEqual(baseline.xray._read(key_path), keys)
        self.assertEqual(self.api("/api/xray/selfhost/setup", dict(self.settings, domain="new.example.org"))[0], 400)
        self.assertEqual(self.api("/api/xray/selfhost/delete", dict(node=self.node_id))[0], 400)
        self.assertEqual(self.api("/api/xray/selfhost/setup", self.settings)[0], 200)
        self.assertEqual(self.api("/api/xray/selfhost/retry", dict(node=self.node_id))[0], 200)
        self.assertEqual(self.api("/api/xray/selfhost/setup", self.settings)[0], 200)
        self.create(selfhost=False, server_name="www.microsoft.com", target="www.microsoft.com:443")
        self.assertEqual(self.api("/api/xray/selfhost/delete", dict(node=self.node_id))[0], 200)
        node = baseline.PANEL.nodes_load(self.deps["nodes_path"])[self.node_id]
        self.assertNotIn("xray_selfhost", node)
        self.assertTrue(node["xray_selfhost_remove"])

    def test_wrong_ready_report_and_protocol_refused(self):
        self.setup_decoy()
        self.ready()
        self.deps["node_snaps"][self.node_id]["xray_selfhost"]["domain"] = "other.example.org"
        self.assertEqual(self.api("/api/xray/set", dict(node=self.node_id, iface="xray1", selfhost=True))[0], 400)
        self.ready()
        self.assertEqual(self.api("/api/xray/set", dict(node=self.node_id, iface="xray1", selfhost=True, protocol="shadowsocks"))[0], 400)

    def test_caddy_does_not_bind_panel_port(self):
        record = selfhost.validate(self.settings)
        config = selfhost.caddy_config(record)
        servers = config["apps"]["http"]["servers"]
        self.assertEqual(servers["decoy"]["listen"], ["127.0.0.1:4443"])
        self.assertEqual(servers["challenge"]["listen"], [":80"])
        issuer = config["apps"]["tls"]["automation"]["policies"][0]["issuers"][0]
        self.assertTrue(issuer["challenges"]["tls-alpn"]["disabled"])
        self.assertTrue(config["admin"]["disabled"])


if __name__ == "__main__":
    unittest.main()
