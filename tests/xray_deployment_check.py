import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.request

root = "/opt/swg-panel-docker"
baseline_path = "/opt/swg-panel-xray/production-baseline.json"


def read(path):
    with open(path) as handle:
        return json.load(handle)


def command(arguments):
    return subprocess.check_output(arguments, text=True)


def api(path, body=None, authed=True):
    headers = {"Content-Type": "application/json"}
    if authed:
        with open(root + "/data/etc/auth") as handle:
            username = handle.read().partition(":")[0].strip()
        with open(root + "/data/lib/session.key", "rb") as handle:
            secret = handle.read().strip()
        payload = (username + "|" + str(int(time.time()) + 600)).encode()
        signature = hmac.new(secret, payload, hashlib.sha256).digest()
        token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode() + "." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        headers["Cookie"] = "swgsess=" + token
    request = urllib.request.Request("http://127.0.0.1:8088" + path, data=None if body is None else json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if isinstance(result, dict) and result.get("ok") is False:
        raise RuntimeError(path + ": " + str(result.get("error")))
    return result


def state():
    response = api("/api/state")
    return response.get("data", response)


def inventories():
    roster = read(root + "/data/lib/users.json")
    nodes = read(root + "/data/lib/nodes.json")
    roster = roster.get("data", roster)
    nodes = nodes.get("data", nodes)
    roster_summary = {key: {identifier: hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
                            for identifier, record in roster.get(key, {}).items()} for key in ("peers", "users")}
    node_map = nodes.get("nodes", nodes)
    node_summary = {identifier: {key: record.get(key) for key in ("token_hash", "token_sha", "ifaces", "wdtt", "csqtt", "xray", "xray_selfhost", "endpoint_host")}
                    for identifier, record in node_map.items() if isinstance(record, dict)}
    return {"roster": roster_summary, "nodes_hash": hashlib.sha256(json.dumps(node_summary, sort_keys=True).encode()).hexdigest(),
            "interfaces": command(["ip", "-o", "link", "show"]).splitlines()}


mode = sys.argv[1] if __name__ == "__main__" else None
if mode == "baseline":
    data = inventories()
    with os.fdopen(os.open(baseline_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        json.dump(data, handle)
    state()
    print("PASS: authenticated API reachable; baseline saved privately")
    print("Existing roster: " + str(len(data["roster"]["users"])) + " users, " + str(len(data["roster"]["peers"])) + " peers")
elif mode == "verify":
    before, after = read(baseline_path), inventories()
    assert before["roster"] == after["roster"], "Existing roster changed"
    assert before["nodes_hash"] == after["nodes_hash"], "Existing node tokens/configs changed"
    before_interfaces = {line.split(": ", 2)[1].split("@")[0] for line in before["interfaces"]}
    after_interfaces = {line.split(": ", 2)[1].split("@")[0] for line in after["interfaces"]}
    assert {name for name in before_interfaces if not name.startswith("veth")} <= after_interfaces, "Existing network interface lost"
    with urllib.request.urlopen("http://127.0.0.1:8088/healthz", timeout=5) as response:
        assert response.status == 200
    try:
        api("/api/state", authed=False)
    except urllib.error.HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Unauthenticated state is not protected")
    data = state()
    nodes = data.get("nodes", [])
    assert nodes and any(node.get("xray_capable") for node in nodes), "Xray-capable node has not synced"
    print("PASS: panel health/auth, node sync, original users/peers/tokens/configs and network interfaces preserved")
elif mode == "describe":
    data = state()
    print("State fields: " + ", ".join(sorted(data)))
    print("Node summary: " + json.dumps([{key: record.get(key) for key in ("id", "name", "online", "xray_capable", "last_seen")} for record in data.get("nodes", [])]))
