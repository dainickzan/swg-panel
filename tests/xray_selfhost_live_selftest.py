import copy
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xray_support as xray
import xray_selfhost as selfhost

directory = "/var/lib/swg-noded"
host_directory = os.environ["SELFHOST_TEST_HOST_DIR"]
assert host_directory.startswith("/tmp/swg-selfhost-test-"), "Isolated test directory required"
namespace = {"STATE_DIR": directory, "SWG_HOST_NODE_DIR": host_directory}


def execute(arguments, **kwargs):
    return subprocess.run(arguments, text=True, capture_output=True, **kwargs)


namespace["run"] = execute
selfhost.CONTAINER = "swg-xray-test-decoy"
iface, client_name = "xray997", "swg-xray-test-selfhost-client"
server_name = "swg-xray-" + iface
tls = directory + "/xray/tls"
os.makedirs(tls, mode=0o700, exist_ok=True)
domain = "decoy.example.org"
certificate, key, ca = tls + "/fullchain.pem", tls + "/key.pem", tls + "/ca.pem"


def required(arguments):
    result = execute(arguments)
    assert not result.returncode, result.stderr
    return result


required(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", tls + "/ca.key", "-out", ca,
          "-subj", "/CN=Isolated test CA", "-days", "3", "-addext", "basicConstraints=critical,CA:TRUE"])
required(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", tls + "/server.csr", "-subj", "/CN=" + domain])
with open(tls + "/extensions", "w") as handle:
    handle.write("subjectAltName=DNS:" + domain + "\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n")
required(["openssl", "x509", "-req", "-in", tls + "/server.csr", "-CA", ca, "-CAkey", tls + "/ca.key", "-CAcreateserial",
          "-out", certificate, "-days", "2", "-extfile", tls + "/extensions"])
os.chmod(key, 0o600)
record = selfhost.validate(dict(domain=domain, mode="certificate", https_port=59445, certificate=certificate, certificate_key=key))
namespace["XRAY_SELFHOST_CA_FILE"] = ca
client_path = directory + "/client.json"
try:
    candidate = directory + "/acme.json"
    xray._write(candidate, selfhost.caddy_config(selfhost.validate(dict(domain=domain, email="test@example.org", https_port=59445))))
    required(["docker", "run", "--rm", "--network", "none", "--mount", "type=bind,src=" + host_directory + "/acme.json,dst=/config.json,readonly",
              selfhost.CADDY_IMAGE, "caddy", "validate", "--config", "/config.json"])
    print("PASS: actual pinned Caddy validates automatic HTTP-01 config without public issuance")
    selfhost.reconcile(record, False, namespace)
    for attempt in range(25):
        report = selfhost.snapshot(namespace)
        if report.get("ready"):
            break
        time.sleep(0.5)
    assert report.get("ready"), report
    assert not selfhost.probe(record)["ready"], "Production must reject isolated private CA"
    assert not selfhost.probe(dict(record, domain="wrong.example.org"), ca)["ready"]
    print("PASS: trusted TLS1.3/h2 works; production trust rejects private CA and wrong hostname")
    identity = required(["docker", "inspect", "--format", "{{.Id}}", selfhost.CONTAINER]).stdout.strip()
    selfhost.reconcile(record, False, namespace)
    assert identity == required(["docker", "inspect", "--format", "{{.Id}}", selfhost.CONTAINER]).stdout.strip()
    selfhost.reconcile(dict(record, certificate=directory + "/xray/tls/missing.pem", restart="invalid-certificate"), False, namespace)
    assert identity == required(["docker", "inspect", "--format", "{{.Id}}", selfhost.CONTAINER]).stdout.strip()
    assert selfhost.probe(record, ca)["ready"]
    selfhost.reconcile(dict(record, restart="recover"), False, namespace)
    for attempt in range(25):
        if selfhost.snapshot(namespace).get("ready"):
            break
        time.sleep(0.5)
    assert selfhost.snapshot(namespace).get("ready")
    print("PASS: invalid certificate candidate preserves previous decoy; Retry restores readiness")
    response = required(["curl", "--silent", "--show-error", "--fail", "--cacert", ca, "--resolve", domain + ":59445:127.0.0.1", "https://" + domain + ":59445/"])
    assert "This website is online" in response.stdout
    config = xray.validate(dict(port=59446, listen="127.0.0.1", endpoint_host="127.0.0.1", server_name=domain, target="127.0.0.1:59445"), {}, {})
    config.update(selfhost=True, api_port=61997, clients=[dict(id=xray.new_credential(), email="selfhost-test@swg")])
    xray.reconcile({iface: config}, [], namespace)
    state = xray._read(directory + "/xray/instances.json")[iface]
    assert not state.get("error"), state.get("error")
    response = required(["curl", "--silent", "--show-error", "--fail", "--retry", "10", "--retry-connrefused", "--retry-delay", "1", "--max-time", "12", "--cacert", ca,
                         "--resolve", domain + ":59446:127.0.0.1", "https://" + domain + ":59446/"])
    assert "This website is online" in response.stdout
    print("PASS: unauthenticated REALITY connections serve our own HTTPS decoy")
    client = {"log": {"loglevel": "warning"}, "inbounds": [{"listen": "127.0.0.1", "port": 59447, "protocol": "socks", "settings": {"udp": True}}],
              "outbounds": [{"protocol": "vless", "settings": {"vnext": [{"address": "127.0.0.1", "port": 59446,
                             "users": [{"id": config["clients"][0]["id"], "encryption": "none", "flow": config["flow"]}]}]},
                             "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": domain,
                             "fingerprint": "chrome", "password": config["public_key"], "shortId": config["short_id"]}}}]}
    xray._write(client_path, client)
    required(["docker", "run", "-d", "--name", client_name, "--network", "host", "--user", "0:0", "--mount", "type=bind,src=" + host_directory + "/client.json,dst=/config.json,readonly",
              xray.XRAY_IMAGE, "run", "-config", "/config.json"])
    time.sleep(1)
    response = required(["curl", "--silent", "--show-error", "--fail", "--max-time", "15", "--proxy", "socks5h://127.0.0.1:59447", "https://example.com/"])
    assert "Example Domain" in response.stdout
    print("PASS: authenticated VLESS/REALITY/Vision transfers HTTPS using self-hosted target")
    original = state["container_id"]
    with patch.object(selfhost, "snapshot", return_value={"ready": False}):
        xray.reconcile({iface: dict(config, restart="must-not-restart")}, [], namespace)
    assert xray._read(directory + "/xray/instances.json")[iface]["container_id"] == original
    assert xray._running(namespace, server_name)
    print("PASS: failed decoy readiness preserves previous Xray container")
    xray.reconcile({}, [iface], namespace)
    selfhost.reconcile(None, True, namespace)
    assert not xray._running(namespace, selfhost.CONTAINER)
    assert os.path.exists(directory + "/xray/decoy/data")
    assert os.path.exists(certificate)
    print("PASS: explicit deletion removes only test containers, retaining certificate data")
finally:
    execute(["docker", "rm", "-f", client_name, server_name, selfhost.CONTAINER])
