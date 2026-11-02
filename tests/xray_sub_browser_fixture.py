import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
import xray_support as xray

directory = "/tmp/swg-xray-sub-fixture"
os.makedirs(directory, exist_ok=True)
token = "isolated-browser-test"
record = xray.validate({"endpoint_host": "203.0.113.10"}, {}, {})
record = xray.public_config(record)
xray._write(directory + "/users.json", {"version": 1, "users": {"test": {"name": "Xray subscription test"}}, "peers": {
    "test-peer": {"id": "test-peer", "user_id": "test", "title": "Xray browser peer", "xray_uuid": xray.new_credential(),
                  "pubkey": "", "targets": [{"node": "test-node", "iface": "xray1", "type": "xray"}]}}})
xray._write(directory + "/nodes.json", {"test-node": {"name": "Xray-Test", "endpoint_host": "203.0.113.10", "xray": {"xray1": record}}})
xray._write(directory + "/subs/users.json", {"test": {"enabled": True, "token_sha": hashlib.sha256(token.encode()).hexdigest()}})
xray._write(directory + "/subs/serve.json", {"enabled": True, "turn_enabled": False, "serve": {"tls_mode": "reverse-proxy"}})
xray._write(directory + "/fleet.json", {"roster_path": directory + "/users.json", "nodes_path": directory + "/nodes.json", "stats_dir": directory})
os.environ.update(SWG_SUB_FLEET=directory + "/fleet.json", SWG_SUB_PORT="18089", SWG_SUB_HOST="0.0.0.0", SWG_SUB_WEB=root)
loader = importlib.machinery.SourceFileLoader("xray_sub_fixture", root + "/swg-sub")
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
module.main()
