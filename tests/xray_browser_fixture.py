import json
import os
import sys
import threading
import time
import urllib.request

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs("/tmp/swg-xray-fixture/stats", exist_ok=True)
json.dump({"roster_path": "/tmp/swg-xray-fixture/users.json", "nodes_path": "/tmp/swg-xray-fixture/nodes.json",
           "stats_dir": "/tmp/swg-xray-fixture/stats", "state_dir": "/tmp/swg-xray-fixture",
           "settings_path": "/tmp/swg-xray-fixture/settings.json"}, open("/tmp/swg-xray-fixture/fleet.json", "w"))
os.environ.update(SWG_PANEL_FLEET="/tmp/swg-xray-fixture/fleet.json", SWG_PANEL_HOST="0.0.0.0", SWG_PANEL_PORT="18088",
                  SWG_PANEL_WEB=root, SWG_PANEL_AUTH="", SWG_PANEL_TLS_CERT="", SWG_PANEL_TLS_KEY="")
sys.path.insert(0, root)
if os.environ.get("SWG_SELFHOST_FIXTURE_READY") == "1":
    import xray_selfhost
    original_resolve = xray_selfhost.resolve
    xray_selfhost.resolve = lambda host: ["203.0.113.10"] if host == "fixture.example.org" else original_resolve(host)


def call(path, body):
    request = urllib.request.Request("http://127.0.0.1:18088" + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(request))


def fixture():
    for attempt in range(120):
        try:
            enrolled = call("/api/nodes/create", {"name": "Xray-Test", "endpoint_host": "203.0.113.10"})["data"]
            break
        except Exception:
            time.sleep(0.5)
    else:
        return
    snapshot = {"hostname": "test-node", "generated_at": int(time.time()), "interfaces": {}, "xray": [], "xray_capable": True, "xray_selfhost_capable": True,
                "node_ips": ["203.0.113.10"], "node_ifaces": [], "turn_manage": "docker", "kind": "docker", "platform": "linux", "arch": "amd64"}
    while True:
        try:
            snapshot["generated_at"] = int(time.time())
            request = urllib.request.Request("http://127.0.0.1:18088/api/node/sync", data=json.dumps({"snapshot": snapshot}).encode(),
                                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + enrolled["token"]})
            reply = json.load(urllib.request.urlopen(request))
            if os.environ.get("SWG_SELFHOST_FIXTURE_READY") == "1" and reply.get("xray_selfhost"):
                snapshot["xray_selfhost"] = dict(reply["xray_selfhost"], ready=True, running=True, status="Ready", error="")
            else:
                snapshot.pop("xray_selfhost", None)
        except Exception as error:
            print("fixture sync:", error, flush=True)
        time.sleep(3)


threading.Thread(target=fixture, daemon=True).start()
path = os.path.join(root, "swg-panel-server")
namespace = {"__name__": "__main__", "__file__": path, "_SWG_REEXEC": True}
exec(compile(open(path).read(), path, "exec"), namespace)
