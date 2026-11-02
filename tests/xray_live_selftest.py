import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xray_support as xray


def execute(arguments, **kwargs):
    return subprocess.run(arguments, text=True, capture_output=True, **kwargs)


directory = tempfile.mkdtemp(prefix="swg-xray-live-", dir="/tmp")
namespace = {"STATE_DIR": directory, "SWG_HOST_NODE_DIR": directory, "run": execute}
xray.capable = lambda unused: True
iface = "xray998"
server_name = "swg-xray-" + iface
client_name = "swg-xray-test-client"
record = xray.validate({"port": 59443, "listen": "127.0.0.1", "endpoint_host": "127.0.0.1"}, {}, {})
record.update(api_port=61998, clients=[{"id": xray.new_credential(), "email": "live-test@swg"}])
client_path = directory + "/client.json"
client = {"log": {"loglevel": "warning"}, "inbounds": [{"listen": "127.0.0.1", "port": 59444, "protocol": "socks", "settings": {"udp": True}}],
          "outbounds": [{"protocol": "vless", "settings": {"vnext": [{"address": "127.0.0.1", "port": 59443,
                         "users": [{"id": record["clients"][0]["id"], "encryption": "none", "flow": record["flow"]}]}]},
                         "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": record["server_name"],
                         "fingerprint": "chrome", "password": record["public_key"], "shortId": record["short_id"]}}}]}


def request():
    return execute(["curl", "--silent", "--show-error", "--fail", "--max-time", "12", "--proxy", "socks5h://127.0.0.1:59444", "https://example.com/"])


try:
    xray.reconcile({iface: record}, [], namespace)
    state = xray._read(directory + "/xray/instances.json")[iface]
    assert not state.get("error"), state.get("error")
    assert xray._running(namespace, server_name), "Xray server did not start"
    json.dump(client, open(client_path, "w"))
    result = execute(["docker", "run", "-d", "--name", client_name, "--network", "host", "--user", "0:0", "--mount",
                      "type=bind,src=" + client_path + ",dst=/config.json,readonly", xray.XRAY_IMAGE, "run", "-config", "/config.json"])
    assert not result.returncode, result.stderr
    time.sleep(2)
    response = request()
    assert not response.returncode and "Example Domain" in response.stdout, response.stderr
    print("PASS: REALITY/Vision client transfers HTTPS through the real Xray server")
    snapshot = xray.snapshot(namespace)[0]
    assert snapshot["rx_bytes"] > 0 and snapshot["tx_bytes"] > 0, snapshot
    assert snapshot["peers"][0]["online"], snapshot
    print("PASS: loopback StatsService reports bytes and recent-traffic presence")
    original = state["container_id"]
    xray.reconcile({iface: record}, [], namespace)
    assert xray._read(directory + "/xray/instances.json")[iface]["container_id"] == original
    print("PASS: unchanged desired state does not restart Xray")
    invalid = dict(record, private_key="invalid")
    xray.reconcile({iface: invalid}, [], namespace)
    assert xray._running(namespace, server_name)
    assert xray._read(directory + "/xray/instances.json")[iface]["error"]
    assert not request().returncode
    print("PASS: invalid config leaves the previously working server intact")
    xray.reconcile({iface: record}, [], namespace)
    assert not xray._read(directory + "/xray/instances.json")[iface]["error"]
    assert xray._read(directory + "/xray/instances.json")[iface]["container_id"] == original
    print("PASS: reverting invalid configuration clears the error without restarting")
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 59445))
        listener.listen()
        xray.reconcile({iface: dict(record, port=59445)}, [], namespace)
        assert xray._read(directory + "/xray/instances.json")[iface]["error"]
        assert xray._read(directory + "/xray/instances.json")[iface]["container_id"] == original
        assert not request().returncode
    print("PASS: occupied TCP port leaves the working server intact")
    xray.reconcile({iface: dict(record, clients=[])}, [], namespace)
    time.sleep(1)
    assert request().returncode != 0
    print("PASS: blocking/removing credentials actually denies client traffic")
    xray.reconcile({iface: record}, [], namespace)
    time.sleep(1)
    assert not request().returncode
    before = xray.snapshot(namespace)[0]["tx_bytes"]
    xray.reconcile({iface: dict(record, restart="test")}, [], namespace)
    time.sleep(1)
    assert not request().returncode
    assert xray.snapshot(namespace)[0]["tx_bytes"] >= before
    print("PASS: unblock restores access and counters survive server restarts")
    xray.reconcile({iface: dict(record, stopped=True)}, [], namespace)
    assert not xray._running(namespace, server_name)
    xray.reconcile({iface: record}, [], namespace)
    time.sleep(1)
    assert not request().returncode
    print("PASS: stop/start controls the real server")
    xray.reconcile({}, [iface], namespace)
    assert not xray._running(namespace, server_name)
    print("PASS: explicit deletion removes only the test Xray container")
finally:
    execute(["docker", "rm", "-f", client_name, server_name])
