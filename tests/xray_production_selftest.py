import json
import os
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xray_support as xray
from xray_deployment_check import api, state

node = next(record["id"] for record in state()["nodes"] if record.get("xray_capable"))
iface = "xray999"
assert iface not in next(record for record in state()["nodes"] if record["id"] == node).get("xray_cfg", {})
directory = tempfile.mkdtemp(prefix="swg-xray-production-test-", dir="/tmp")
client_name = "swg-xray-production-test-client"
server_name = "swg-xray-" + iface
user_id = peer_id = None
created = False


def execute(arguments):
    return subprocess.run(arguments, text=True, capture_output=True, timeout=30)


def wait(predicate, label):
    for attempt in range(60):
        if predicate():
            return
        time.sleep(1)
    raise AssertionError("Timed out: " + label)


def running():
    result = execute(["docker", "inspect", "--format", "{{.State.Running}}", server_name])
    return not result.returncode and result.stdout.strip() == "true"


def credentials():
    try:
        config = xray._read("/opt/swg-panel-docker/data/node/xray/" + iface + ".json")
        return {client.get("id", client.get("password")) for inbound in config.get("inbounds", []) for client in inbound.get("settings", {}).get("clients", [])}
    except OSError:
        return set()


def start_client():
    uri = api("/api/xray/config?peer_id=" + peer_id + "&node=" + node + "&iface=" + iface)["data"]["uri"]
    parsed = urlsplit(uri)
    query = parse_qs(parsed.query)
    config = {"log": {"loglevel": "warning"}, "inbounds": [{"listen": "127.0.0.1", "port": 59449, "protocol": "socks"}],
              "outbounds": [{"protocol": "vless", "settings": {"vnext": [{"address": "127.0.0.1", "port": 59448,
                             "users": [{"id": parsed.username, "encryption": "none", "flow": "xtls-rprx-vision"}]}]},
                             "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {"serverName": query["sni"][0],
                             "fingerprint": "chrome", "password": query["pbk"][0], "shortId": query["sid"][0]}}}]}
    execute(["docker", "rm", "-f", client_name])
    xray._write(directory + "/client.json", config)
    result = execute(["docker", "run", "-d", "--name", client_name, "--network", "host", "--user", "0:0", "--mount",
                      "type=bind,src=" + directory + "/client.json,dst=/config.json,readonly", xray.XRAY_IMAGE, "run", "-config", "/config.json"])
    assert not result.returncode, result.stderr
    time.sleep(1)
    return parsed.username


def request():
    return execute(["curl", "--fail", "--silent", "--socks5-hostname", "127.0.0.1:59449", "--max-time", "6", "https://example.com"])


try:
    api("/api/xray/set", {"node": node, "iface": iface, "listen": "127.0.0.1", "endpoint_host": "127.0.0.1", "port": 59448, "title": "Temporary validation"})
    created = True
    user_id = api("/api/users/create", {"name": "Temporary Xray validation"})["data"]["id"]
    peer = api("/api/peers/create", {"title": "Temporary Xray validation", "user_id": user_id, "targets": [{"node": node, "iface": iface}]})["data"]
    peer_id, credential = peer["id"], peer["xray_uuid"]
    wait(lambda: running() and credential in credentials(), "node deploys API-created interface and peer")
    assert start_client() == credential
    wait(lambda: not request().returncode, "initial client traffic")
    print("PASS: deployed panel API -> node sync -> REALITY/Vision HTTPS traffic", flush=True)
    wait(lambda: any(row.get("tx_bytes", 0) > 0 for row in state().get("snapshots", {}).get(node, {}).get("xray", []) if row.get("iface") == iface), "panel telemetry")
    print("PASS: production panel receives Xray traffic counters", flush=True)
    api("/api/peers/block", {"peer_id": peer_id})
    wait(lambda: credential not in credentials(), "blocked peer removed")
    assert request().returncode
    api("/api/peers/unblock", {"peer_id": peer_id})
    wait(lambda: credential in credentials(), "unblock")
    wait(lambda: not request().returncode, "peer unblock traffic")
    print("PASS: shared peer block/unblock changes real client access", flush=True)
    api("/api/user/block", {"user_id": user_id})
    wait(lambda: credential not in credentials(), "blocked owner removed")
    assert request().returncode
    api("/api/user/unblock", {"user_id": user_id})
    wait(lambda: credential in credentials(), "owner unblock")
    wait(lambda: not request().returncode, "user unblock traffic")
    print("PASS: shared user block/unblock changes real client access", flush=True)
    replacement = api("/api/xray-peer/rotate", {"peer_id": peer_id})["data"]["xray_uuid"]
    wait(lambda: replacement in credentials() and credential not in credentials(), "credential rotation")
    assert request().returncode
    assert start_client() == replacement
    wait(lambda: not request().returncode, "rotated client traffic")
    print("PASS: old link revoked; newly generated link works", flush=True)
    api("/api/xray/action", {"node": node, "iface": iface, "action": "stop"})
    wait(lambda: not running(), "stop")
    api("/api/xray/action", {"node": node, "iface": iface, "action": "start"})
    wait(running, "start")
    wait(lambda: not request().returncode, "start traffic")
    print("PASS: production interface stop/start", flush=True)
finally:
    execute(["docker", "rm", "-f", client_name])
    if peer_id:
        api("/api/peers/unassign", {"peer_id": peer_id})
        api("/api/peers/delete", {"peer_id": peer_id})
    if user_id:
        api("/api/users/delete", {"id": user_id})
    if created:
        api("/api/xray/delete", {"node": node, "iface": iface})
        wait(lambda: not running(), "API deletion")
    print("Temporary production test user, peer, interface and containers removed", flush=True)
