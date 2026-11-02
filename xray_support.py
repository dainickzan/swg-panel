import base64
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import time
import uuid
import xray_selfhost
from urllib.parse import urlencode, quote

XRAY_IMAGE = "ghcr.io/xtls/xray-core:26.10.31@sha256:ceabeac03a89d991d2a247361825c1d1d2e9c91cd5dc010f298c7dc0c2f325c9"
XRAY_NAME = re.compile(r"^xray[0-9]{1,3}$")
PROTOCOLS = ("vless", "vmess", "trojan", "shadowsocks")
PUBLIC_FIELDS = ("protocol", "listen", "port", "endpoint_host", "security", "server_name", "target", "public_key",
                 "short_id", "transport", "path", "flow", "method", "title", "stopped", "blocked_domains", "blocked_ips",
                 "certificate", "certificate_key", "egress_type", "egress_host", "egress_port", "egress_user", "selfhost")


def new_credential():
    return str(uuid.uuid4())


def reality_keys():
    private = subprocess.check_output(["openssl", "genpkey", "-algorithm", "X25519", "-outform", "DER"], stderr=subprocess.DEVNULL)
    public = subprocess.check_output(["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"], input=private, stderr=subprocess.DEVNULL)
    def encode(value):
        return base64.urlsafe_b64encode(value[-32:]).decode().rstrip("=")
    return encode(private), encode(public)


def public_config(record):
    return {key: record[key] for key in PUBLIC_FIELDS if key in record}


def client_uri(record, credential, label="Xray"):
    protocol = record.get("protocol", "vless")
    host = record.get("endpoint_host") or record.get("listen") or ""
    host = "[" + host + "]" if ":" in host and not host.startswith("[") else host
    port = record["port"]
    if protocol == "vmess":
        payload = {"v": "2", "ps": label, "add": host.strip("[]"), "port": str(port), "id": credential,
                   "aid": "0", "scy": "auto", "net": record.get("transport", "tcp"), "type": "none",
                   "host": record.get("server_name", ""), "path": record.get("path", "/"), "tls": "tls", "sni": record.get("server_name", "")}
        return "vmess://" + base64.b64encode(json.dumps(payload).encode()).decode()
    if protocol == "shadowsocks":
        login = base64.urlsafe_b64encode((record.get("method", "chacha20-ietf-poly1305") + ":" + credential).encode()).decode().rstrip("=")
        return "ss://" + login + "@" + host + ":" + str(port) + "#" + quote(label, safe="")
    query = {"type": record.get("transport", "tcp"), "security": record.get("security", "reality")}
    if protocol == "vless":
        query["encryption"] = "none"
    if record.get("server_name"):
        query["sni"] = record["server_name"]
    if record.get("security") == "reality":
        query.update({"pbk": record["public_key"], "sid": record["short_id"], "fp": "chrome", "spx": "/"})
    if record.get("flow"):
        query["flow"] = record["flow"]
    if record.get("transport") == "ws":
        query.update({"path": record.get("path", "/"), "host": record.get("server_name", "")})
    return protocol + "://" + quote(credential, safe="") + "@" + host + ":" + str(port) + "?" + urlencode(query) + "#" + quote(label, safe="")


def desired(roster, nodes, node_id, now=None, keys=None):
    now = int(time.time()) if now is None else now
    users = roster.get("users") or {}
    output = {}
    for iface, record in ((nodes.get(node_id) or {}).get("xray") or {}).items():
        instance = dict(record, clients=[])
        instance.update((keys or {}).get(node_id + "/" + iface) or {})
        for peer_id, peer in (roster.get("peers") or {}).items():
            owner = users.get(peer.get("user_id")) or {}
            if not peer.get("xray_uuid") or peer.get("disabled") or owner.get("disabled"):
                continue
            if any(int(item.get("expiry") or 0) and now >= int(item["expiry"]) for item in (peer, owner)):
                continue
            if any(target.get("node") == node_id and target.get("iface") == iface for target in peer.get("targets", [])):
                instance["clients"].append({"id": peer["xray_uuid"], "email": peer_id + "@swg"})
        output[iface] = instance
    return output


def server_config(record):
    protocol = record["protocol"]
    clients = []
    for client in record.get("clients", []):
        entry = {"email": client["email"], "level": 0}
        entry["password" if protocol in ("trojan", "shadowsocks") else "id"] = client["id"]
        if protocol == "vless" and record.get("flow"):
            entry["flow"] = record["flow"]
        if protocol == "shadowsocks":
            entry["method"] = record["method"]
        clients.append(entry)
    settings = {"clients": clients}
    if protocol == "vless":
        settings["decryption"] = "none"
    if protocol == "shadowsocks":
        settings.update({"network": "tcp,udp", "method": record["method"]})
    stream = {"network": record.get("transport", "tcp"), "security": record.get("security", "reality")}
    if stream["security"] == "reality":
        stream["realitySettings"] = {"show": False, "target": record["target"], "xver": 0,
                                     "serverNames": [record["server_name"]], "privateKey": record["private_key"], "shortIds": [record["short_id"]]}
    elif stream["security"] == "tls":
        stream["tlsSettings"] = {"minVersion": "1.2", "certificates": [{"certificateFile": record["certificate"], "keyFile": record["certificate_key"]}]}
    if stream["network"] == "ws":
        stream["wsSettings"] = {"path": record.get("path", "/")}
    inbounds = [{"tag": "proxy", "listen": record.get("listen", "0.0.0.0"), "port": record["port"], "protocol": protocol,
                 "settings": settings, "streamSettings": stream, "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}},
                {"tag": "api", "listen": "127.0.0.1", "port": record["api_port"], "protocol": "dokodemo-door", "settings": {"address": "127.0.0.1"}}]
    if not clients:
        inbounds = inbounds[1:]
    outbound = {"tag": "direct", "protocol": "freedom", "settings": {}}
    if record.get("egress_type") in ("socks", "http"):
        server = {"address": record["egress_host"], "port": record["egress_port"]}
        if record.get("egress_user"):
            server["users"] = [{"user": record["egress_user"], "pass": record.get("egress_password", "")}]
        outbound = {"tag": "direct", "protocol": record["egress_type"], "settings": {"servers": [server]}}
    rules = [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]
    if record.get("blocked_domains"):
        rules.append({"type": "field", "domain": ["domain:" + domain for domain in record["blocked_domains"]], "outboundTag": "blocked"})
    if record.get("blocked_ips"):
        rules.append({"type": "field", "ip": record["blocked_ips"], "outboundTag": "blocked"})
    return {"log": {"loglevel": "warning", "access": "none"}, "api": {"tag": "api", "services": ["StatsService"]}, "stats": {},
            "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}}, "inbounds": inbounds,
            "outbounds": [outbound, {"tag": "blocked", "protocol": "blackhole"}], "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules}}


def _host(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        return False
    try:
        return not ipaddress.ip_address(value).is_unspecified
    except ValueError:
        return bool(re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", value))


def validate(body, existing, node):
    record = dict(existing)
    for key in PUBLIC_FIELDS + ("egress_password",):
        if key in body:
            record[key] = body[key]
    defaults = {"protocol": "vless", "security": "reality", "transport": "tcp", "listen": "0.0.0.0", "server_name": "www.microsoft.com",
                "path": "/", "method": "chacha20-ietf-poly1305", "egress_type": "direct", "endpoint_host": node.get("endpoint_host") or ""}
    for key, value in defaults.items():
        record.setdefault(key, value)
    record.setdefault("target", record["server_name"] + ":443")
    record.setdefault("flow", "xtls-rprx-vision" if record["security"] == "reality" else "")
    if record["protocol"] not in PROTOCOLS or record["transport"] not in ("tcp", "ws") or record["security"] not in ("reality", "tls", "none"):
        raise ValueError("Unsupported Xray protocol, transport or security")
    record["port"] = int(record.get("port", 10443))
    if not isinstance(record.get("stopped", False), bool) or not 1 <= record["port"] <= 65535:
        raise ValueError("Invalid stopped flag or port (1..65535)")
    ipaddress.ip_address(record["listen"])
    if not _host(record["endpoint_host"]):
        raise ValueError("A valid public endpoint IP or hostname is required")
    if record["flow"] not in ("", "xtls-rprx-vision") or (record["flow"] and (record["protocol"] != "vless" or record["transport"] != "tcp" or record["security"] == "none")):
        raise ValueError("Vision flow requires VLESS over TCP with TLS or REALITY")
    if record["security"] == "reality":
        if record["protocol"] != "vless" or record["transport"] != "tcp":
            raise ValueError("REALITY requires VLESS over TCP")
        host, separator, port = str(record["target"]).rpartition(":")
        if not separator or not _host(host.strip("[]")) or not port.isdigit() or not 1 <= int(port) <= 65535 or not _host(record["server_name"]):
            raise ValueError("REALITY requires a valid SNI and target host:port")
        if "private_key" not in record:
            record["private_key"], record["public_key"] = reality_keys()
        record.setdefault("short_id", secrets.token_hex(8))
        if not isinstance(record["short_id"], str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}){1,8}", record["short_id"]):
            raise ValueError("REALITY short ID must contain 2..16 even hex digits")
    elif record["security"] == "tls":
        if not _host(record["server_name"]):
            raise ValueError("TLS requires a valid server name")
        for field in ("certificate", "certificate_key"):
            path = record.get(field) or ""
            if not isinstance(path, str) or not path.startswith("/var/lib/swg-noded/xray/tls/") or ".." in path.split("/") or "," in path or any(ord(char) < 32 for char in path):
                raise ValueError("TLS certificate paths must be under /var/lib/swg-noded/xray/tls/")
        if record["protocol"] == "shadowsocks":
            raise ValueError("Shadowsocks uses its own encryption; select security=none")
    elif record["protocol"] != "shadowsocks":
        raise ValueError("Unencrypted proxy protocols are refused; use TLS or REALITY")
    if record["protocol"] == "shadowsocks" and (record["transport"] != "tcp" or record["method"] not in ("aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305")):
        raise ValueError("Unsupported Shadowsocks transport or method")
    if not isinstance(record["path"], str) or not record["path"].startswith("/") or len(record["path"]) > 512:
        raise ValueError("WebSocket path must start with /")
    for key in ("title", "egress_user", "egress_password"):
        if key in record and (not isinstance(record[key], str) or len(record[key]) > 512 or any(ord(char) < 32 for char in record[key])):
            raise ValueError("Invalid " + key)
    for field in ("blocked_domains", "blocked_ips"):
        values = record.get(field) or []
        if not isinstance(values, list) or len(values) > 1000 or any(not isinstance(value, str) for value in values):
            raise ValueError(field + " must be a list of at most 1000 entries")
        record[field] = list(dict.fromkeys(values))
    if any(not _host(domain) or ":" in domain for domain in record["blocked_domains"]):
        raise ValueError("Invalid blocked domain")
    record["blocked_ips"] = [str(ipaddress.ip_network(network, strict=False)) for network in record["blocked_ips"]]
    if record["egress_type"] not in ("direct", "socks", "http"):
        raise ValueError("Unsupported egress type")
    if record["egress_type"] != "direct":
        if not _host(record.get("egress_host")) or not 1 <= int(record.get("egress_port") or 0) <= 65535:
            raise ValueError("Proxy egress requires a valid host and port")
        record["egress_port"] = int(record["egress_port"])
    return record


def panel_api(method, path, query, body, deps, namespace):
    nodes = namespace["nodes_load"](deps["nodes_path"])
    snapshots = deps.get("node_snaps") or {}
    roster_path = deps["roster_path"]
    if method == "POST" and path.startswith("/api/xray/selfhost/"):
        return xray_selfhost.panel_api(path, body, deps, namespace, nodes)
    key_path = os.path.join(os.path.dirname(roster_path), "xray-keys.json")
    key_store = _read(key_path)
    if method == "GET" and path == "/api/xray/config":
        peer_id = (query.get("peer_id") or [""])[0]
        node_id = (query.get("node") or [""])[0]
        iface = (query.get("iface") or [""])[0]
        peer = namespace["roster_load"](roster_path)["peers"].get(peer_id) or {}
        record = ((nodes.get(node_id) or {}).get("xray") or {}).get(iface)
        if not record or not peer.get("xray_uuid") or not any(target.get("node") == node_id and target.get("iface") == iface for target in peer.get("targets", [])):
            return 404, {"ok": False, "error": "Unknown Xray deployment"}
        return 200, {"ok": True, "data": {"uri": client_uri(record, peer["xray_uuid"], peer.get("title") or iface)}}
    if method != "POST":
        return 405, {"ok": False, "error": "Method not allowed"}
    if path == "/api/xray-peer/rotate":
        roster = namespace["roster_load"](roster_path)
        peer = roster["peers"].get(body.get("peer_id"))
        if not peer or not peer.get("xray_uuid"):
            return 404, {"ok": False, "error": "Unknown Xray peer"}
        peer["xray_uuid"] = new_credential()
        peer["modified_at"] = int(time.time())
        namespace["roster_save"](roster_path, roster)
        namespace["ev_append"](roster_path, "peer", peer["id"], "Rotated Xray credentials", peer.get("title") or peer["id"])
        return 200, {"ok": True, "data": peer}
    node_id, iface = body.get("node"), body.get("iface")
    if node_id not in nodes or not isinstance(iface, str) or not XRAY_NAME.fullmatch(iface):
        return 400, {"ok": False, "error": "A known node and interface name xray0..xray999 are required"}
    node = nodes[node_id]
    instances = node.setdefault("xray", {})
    existing = instances.get(iface)
    if path == "/api/xray/delete":
        if not existing:
            return 404, {"ok": False, "error": "Unknown Xray interface"}
        roster = namespace["roster_load"](roster_path)
        for peer in roster["peers"].values():
            peer["targets"] = [target for target in peer.get("targets", []) if not (target.get("node") == node_id and target.get("iface") == iface)]
        namespace["roster_save"](roster_path, roster)
        del instances[iface]
        key_store.pop(node_id + "/" + iface, None)
        _write(key_path, key_store)
        node.setdefault("xray_delete", {})[iface] = int(time.time())
    elif path == "/api/xray/action":
        if not existing:
            return 404, {"ok": False, "error": "Unknown Xray interface"}
        action = body.get("action")
        if action not in ("start", "stop", "restart"):
            return 400, {"ok": False, "error": "Action must be start, stop or restart"}
        existing["stopped"] = action == "stop"
        if action == "restart":
            existing["restart"] = secrets.token_hex(8)
    elif path == "/api/xray/set":
        if not snapshots.get(node_id, {}).get("xray_capable"):
            return 400, {"ok": False, "error": "This node needs the Xray-capable swg-node build and Docker management"}
        if not existing and any(iface in (node.get(kind) or {}) for kind in ("ifaces", "wdtt", "csqtt")):
            return 409, {"ok": False, "error": "Interface name is already in use"}
        try:
            prior = dict(existing or {})
            prior.update(key_store.get(node_id + "/" + iface) or {})
            record = validate(body, prior, node)
            if body.get("selfhost", prior.get("selfhost", False)):
                decoy = node.get("xray_selfhost") or {}
                report = snapshots.get(node_id, {}).get("xray_selfhost") or {}
                if not report.get("ready") or report.get("domain") != decoy.get("domain") or report.get("https_port") != decoy.get("https_port"):
                    raise ValueError("Set up the self-hosted decoy and wait for trusted HTTPS to be Ready before applying it")
                if record["protocol"] != "vless" or record["security"] != "reality" or record["transport"] != "tcp":
                    raise ValueError("Self-hosted REALITY requires VLESS / REALITY / TCP")
                record.update(selfhost=True, server_name=decoy["domain"], target="127.0.0.1:" + str(decoy["https_port"]))
            else:
                record.pop("selfhost", None)
            used = namespace["_node_ports"](deps, nodes, node_id, skip=("xray", iface))
            if record["port"] in used:
                raise ValueError("Port is already used by " + str(used[record["port"]]))
            record.setdefault("api_port", 61000 + int(iface[4:]))
            if record["api_port"] == record["port"] or record["api_port"] in used:
                raise ValueError("Xray statistics port is already in use; choose another interface name")
            if existing and body.get("public_key", existing.get("public_key")) != existing.get("public_key"):
                raise ValueError("REALITY public key is derived from its private key and cannot be edited")
        except (ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
            return 400, {"ok": False, "error": str(error)}
        private = {key: record.pop(key) for key in ("private_key", "egress_password") if key in record}
        key_store[node_id + "/" + iface] = private
        _write(key_path, key_store)
        instances[iface] = record
        (node.get("xray_delete") or {}).pop(iface, None)
    else:
        return 404, {"ok": False, "error": "Unknown Xray endpoint"}
    namespace["nodes_save"](deps["nodes_path"], nodes)
    namespace["ev_append"](roster_path, "iface", node_id + "/" + iface, "Xray " + path.rsplit("/", 1)[-1], iface)
    return 200, {"ok": True, "data": public_config(instances.get(iface) or {})}


def _read(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def _write(path, value):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + ".tmp"
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _docker(namespace, arguments, timeout=30):
    result = namespace["run"](["docker"] + arguments, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


def _running(namespace, name):
    result = _docker(namespace, ["inspect", "--format", "{{.State.Running}}", name], timeout=5)
    return result[0] == 0 and result[1].strip() == "true"


def host_node_dir(namespace):
    return namespace.get("SWG_HOST_NODE_DIR") or (namespace.get("STATE_DIR", "") if namespace.get("NODE_KIND") != "docker" else "")


def capable(namespace):
    return bool(host_node_dir(namespace) and os.path.exists("/var/run/docker.sock"))


def reconcile(want, remove, namespace):
    if not isinstance(want, dict) or not capable(namespace):
        return
    directory = os.path.join(namespace["STATE_DIR"], "xray")
    state_path = os.path.join(directory, "instances.json")
    state = _read(state_path)
    for iface in remove or []:
        if XRAY_NAME.fullmatch(str(iface)) and iface not in want:
            _docker(namespace, ["rm", "-f", "swg-xray-" + iface])
            state.pop(iface, None)
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(directory, iface + ".json"))
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(directory, iface + ".candidate.json"))
    for iface, record in want.items():
        if not XRAY_NAME.fullmatch(iface) or not isinstance(record, dict):
            continue
        name = "swg-xray-" + iface
        previous = state.get(iface) or {}
        try:
            config = server_config(record)
            signature = hashlib.sha256((json.dumps(config, sort_keys=True) + str(record.get("restart", ""))).encode()).hexdigest()
            if record.get("stopped"):
                if _running(namespace, name):
                    _docker(namespace, ["stop", name])
                state[iface] = dict(previous, config=public_config(record), stopped=True, error="")
                continue
            if record.get("selfhost"):
                decoy = xray_selfhost.snapshot(namespace)
                if not decoy.get("ready") or record["server_name"] != decoy.get("domain") or record["target"] != "127.0.0.1:" + str(decoy.get("https_port")):
                    raise ValueError("Self-hosted decoy HTTPS is not ready; previous Xray container is preserved")
            if signature == previous.get("signature") and _running(namespace, name):
                state[iface] = dict(previous, config=public_config(record), error="", stopped=False)
                continue
            running = _running(namespace, name)
            prior = previous.get("config") or {}
            for port in (record["port"], record["api_port"]):
                if running and port in (prior.get("port"), previous.get("api_port")):
                    continue
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        listener.bind(("0.0.0.0", port))
                    except OSError as error:
                        raise ValueError("TCP port " + str(port) + " is unavailable: " + str(error)) from error
            if record["protocol"] == "shadowsocks" and not (running and prior.get("protocol") == "shadowsocks" and prior.get("port") == record["port"]):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
                    try:
                        listener.bind(("0.0.0.0", record["port"]))
                    except OSError as error:
                        raise ValueError("UDP port " + str(record["port"]) + " is unavailable: " + str(error)) from error
            candidate = os.path.join(directory, iface + ".candidate.json")
            _write(candidate, config)
            host_dir = host_node_dir(namespace).rstrip("/") + "/xray"
            mounts = ["--mount", "type=bind,src=" + host_dir + "/" + iface + ".candidate.json,dst=/config.json,readonly"]
            for key in ("certificate", "certificate_key"):
                if record.get("security") == "tls":
                    path = record[key]
                    source = host_node_dir(namespace) + path[len(namespace["STATE_DIR"]):] if path.startswith(namespace["STATE_DIR"].rstrip("/") + "/") else path
                    mounts += ["--mount", "type=bind,src=" + source + ",dst=" + path + ",readonly"]
            test = _docker(namespace, ["run", "--rm", "--network", "none", "--user", "0:0"] + mounts + [XRAY_IMAGE, "run", "-test", "-config", "/config.json"], timeout=180)
            if test[0]:
                raise ValueError((test[2] or test[1] or "Xray configuration test failed")[-1200:])
            os.replace(candidate, os.path.join(directory, iface + ".json"))
            mounts[1] = "type=bind,src=" + host_dir + "/" + iface + ".json,dst=/config.json,readonly"
            _docker(namespace, ["rm", "-f", name])
            start = _docker(namespace, ["run", "-d", "--name", name, "--network", "host", "--restart", "unless-stopped", "--user", "0:0",
                                        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only", "--log-opt", "max-size=5m", "--log-opt", "max-file=2"] + mounts + [XRAY_IMAGE, "run", "-config", "/config.json"])
            if start[0]:
                raise ValueError((start[2] or start[1])[-1200:])
            state[iface] = {"signature": signature, "config": public_config(record), "api_port": record["api_port"],
                            "clients": [client["email"].removesuffix("@swg") for client in record.get("clients", [])],
                            "error": "", "stopped": False, "container_id": start[1].strip()}
        except (ValueError, KeyError, TypeError, OSError) as error:
            state[iface] = dict(previous, config=previous.get("config") or public_config(record), attempted_config=public_config(record), error=str(error), attempted_at=time.time())
    _write(state_path, state)


def snapshot(namespace):
    if not capable(namespace):
        return []
    directory = os.path.join(namespace["STATE_DIR"], "xray")
    states = _read(os.path.join(directory, "instances.json"))
    ledger_path = os.path.join(directory, "traffic.json")
    ledger = _read(ledger_path)
    output, now = [], time.time()
    for iface, state in states.items():
        running = _running(namespace, "swg-xray-" + iface)
        peers, counters = [], {}
        if running and state.get("api_port"):
            response = _docker(namespace, ["exec", "swg-xray-" + iface, "/usr/local/bin/xray", "api", "statsquery", "--server=127.0.0.1:" + str(state["api_port"]), "-pattern", "user>>>"], timeout=5)
            if response[0] == 0:
                try:
                    for stat in json.loads(response[1]).get("stat", []):
                        match = re.fullmatch(r"user>>>(.+)@swg>>>traffic>>>(uplink|downlink)", stat.get("name", ""))
                        if match:
                            counters.setdefault(match[1], {})[match[2]] = int(stat.get("value", 0))
                except (ValueError, TypeError):
                    pass
        active = set(state.get("clients", []))
        known = active | {key.split("/", 1)[1] for key in ledger if key.startswith(iface + "/")}
        for peer_id in sorted(known):
            key = iface + "/" + peer_id
            before, current = ledger.get(key) or {}, counters.get(peer_id, {})
            elapsed = max(1, now - before.get("at", now))
            row = {"peer_id": peer_id, "rx_bytes": before.get("rx_bytes", 0), "tx_bytes": before.get("tx_bytes", 0), "rx_rate": 0, "tx_rate": 0}
            same_process, moved = before.get("container_id") == state.get("container_id"), False
            measured = running and bool(current)
            for direction, raw_key in (("rx", "uplink"), ("tx", "downlink")):
                counter = current.get(raw_key, 0)
                previous = before.get(raw_key, 0) if same_process else 0
                delta = max(0, counter - previous) if counter >= previous else counter
                if measured:
                    row[direction + "_bytes"] += delta
                    row[direction + "_rate"] = delta / elapsed
                    moved = moved or delta > 0
                else:
                    current[raw_key] = previous
            last_seen = now if moved else before.get("last_seen", 0)
            row.update(online=bool(peer_id in active and running and last_seen and now - last_seen < 120), last_seen=last_seen)
            ledger[key] = dict(row, **current, at=now, container_id=state.get("container_id"))
            peers.append(row)
        row = dict(state.get("config") or {}, iface=iface, running=running, error=state.get("error", ""), peers=peers)
        row.update({field: sum(peer[field] for peer in peers) for field in ("rx_bytes", "tx_bytes", "rx_rate", "tx_rate")})
        output.append(row)
    if states:
        _write(ledger_path, ledger)
    return output
