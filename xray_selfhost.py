import contextlib
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import time

CADDY_IMAGE = "caddy:2.11.2-alpine@sha256:834468128c7696cec0ceea6172f7d692daf645ae51983ca76e39da54a97c570d"
CONTAINER = "swg-xray-decoy"
FIELDS = ("domain", "mode", "email", "https_port", "http_port", "certificate", "certificate_key", "restart")
DOMAIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.I)


def domain_name(value):
    domain = str(value or "").strip().lower().rstrip(".")
    if not DOMAIN.fullmatch(domain) or domain.endswith((".local", ".localhost", ".invalid", ".test")):
        raise ValueError("Enter a public domain you control, not an IP, URL or single-label hostname")
    return domain


def validate(body):
    record = {key: body[key] for key in FIELDS if key in body}
    record["domain"] = domain_name(record.get("domain"))
    record.setdefault("mode", "acme")
    record["https_port"] = int(record.get("https_port") or 4443)
    if not 1024 <= record["https_port"] <= 59999:
        raise ValueError("Decoy HTTPS port must be 1024..59999")
    record["http_port"] = 80 if record["mode"] == "acme" else None
    if record["mode"] == "acme":
        email = str(record.get("email") or "").strip()
        if not re.fullmatch(r"[^\s@<>]{1,128}@[^\s@<>]{1,128}\.[^\s@<>]{2,63}", email):
            raise ValueError("Enter an email address for certificate expiry notices")
        record["email"] = email
        record.pop("certificate", None)
        record.pop("certificate_key", None)
    elif record["mode"] == "certificate":
        record.pop("email", None)
        for field in ("certificate", "certificate_key"):
            path = record.get(field)
            if not isinstance(path, str) or not path.startswith("/var/lib/swg-noded/xray/tls/") or ".." in path.split("/") or "," in path or any(ord(char) < 32 for char in path):
                raise ValueError("Certificate paths must be under /var/lib/swg-noded/xray/tls/")
    else:
        raise ValueError("Certificate mode must be acme or certificate")
    return record


def resolve(host):
    return sorted({str(ipaddress.ip_address(item[4][0])) for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})


def dns_check(domain, endpoint):
    try:
        addresses, expected = resolve(domain), resolve(endpoint)
    except (OSError, ValueError) as error:
        return {"ok": False, "domain": domain, "addresses": [], "expected": [], "message": "DNS lookup failed: " + str(error)}
    matches = bool(addresses) and set(addresses) <= set(expected)
    return {"ok": matches, "domain": domain, "addresses": addresses, "expected": expected,
            "message": "All domain A/AAAA records point directly to this node" if matches else "Point every A/AAAA record directly to this node; disable CDN proxying and remove incorrect IPv6 records"}


def panel_api(path, body, deps, namespace, nodes):
    node_id = body.get("node")
    node = nodes.get(node_id)
    if not node:
        return 400, {"ok": False, "error": "A known node is required"}
    snapshot = (deps.get("node_snaps") or {}).get(node_id) or {}
    if not snapshot.get("xray_selfhost_capable"):
        return 400, {"ok": False, "error": "Update this node to the self-hosted REALITY build first"}
    existing = node.get("xray_selfhost") or {}
    try:
        if path == "/api/xray/selfhost/check":
            domain = domain_name(body.get("domain"))
            return 200, {"ok": True, "data": dns_check(domain, node.get("endpoint_host") or "")}
        if path == "/api/xray/selfhost/setup":
            record = validate(body)
            changed = record != {key: value for key, value in existing.items() if key != "restart"}
            referenced = any(config.get("selfhost") for config in (node.get("xray") or {}).values())
            if existing and changed and referenced:
                raise ValueError("This decoy is used by Xray interfaces. Switch them to another target before changing its settings")
            result = dns_check(record["domain"], node.get("endpoint_host") or "")
            if not result["ok"]:
                return 400, {"ok": False, "error": result["message"], "data": result}
            used = namespace["_node_ports"](deps, nodes, node_id, skip=("xray_selfhost", "decoy"))
            reported = set(snapshot.get("xray_tcp_ports") or [])
            for field in ("http_port", "https_port"):
                port = record.get(field)
                if not port:
                    continue
                if port in used or (port in reported and port != existing.get(field)):
                    raise ValueError("Port " + str(port) + " is already occupied; no existing service will be stopped")
            if not changed:
                record.update({key: existing[key] for key in ("restart",) if key in existing})
            node["xray_selfhost"] = record
            node.pop("xray_selfhost_remove", None)
        elif path == "/api/xray/selfhost/retry":
            if not existing:
                raise ValueError("Set up the decoy first")
            node["xray_selfhost"]["restart"] = str(time.time_ns())
        elif path == "/api/xray/selfhost/delete":
            if any(config.get("selfhost") for config in (node.get("xray") or {}).values()):
                raise ValueError("Switch every Xray interface away from this decoy before deleting it")
            node.pop("xray_selfhost", None)
            node["xray_selfhost_remove"] = int(time.time())
        else:
            return 404, {"ok": False, "error": "Unknown self-hosted REALITY endpoint"}
    except (ValueError, TypeError, OSError) as error:
        return 400, {"ok": False, "error": str(error)}
    namespace["nodes_save"](deps["nodes_path"], nodes)
    namespace["ev_append"](deps["roster_path"], "iface", node_id + "/decoy", "Self-hosted REALITY " + path.rsplit("/", 1)[-1], node.get("name") or node_id)
    return 200, {"ok": True, "data": node.get("xray_selfhost") or {}}


def tcp_ports():
    ports = set()
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(filename) as handle:
                for line in list(handle)[1:]:
                    columns = line.split()
                    if len(columns) > 3 and columns[3] == "0A":
                        ports.add(int(columns[1].split(":")[1], 16))
        except (OSError, ValueError):
            pass
    return sorted(ports)


def caddy_config(record):
    domain = record["domain"]
    page = "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Welcome</title><style>body{font:18px system-ui;margin:12vh auto;padding:24px;max-width:680px;color:#263238}h1{font-size:42px}</style><h1>Welcome</h1><p>This website is online.</p></html>"
    routes = [{"match": [{"host": [domain]}], "handle": [{"handler": "static_response", "headers": {"Content-Type": ["text/html; charset=utf-8"]}, "body": page}]}]
    servers = {"decoy": {"listen": ["127.0.0.1:" + str(record["https_port"])], "protocols": ["h1", "h2"], "routes": routes,
                         "tls_connection_policies": [{"match": {"sni": [domain]}, "protocol_min": "tls1.3"}],
                         "automatic_https": {"disable_redirects": True}}}
    tls = {}
    if record["mode"] == "acme":
        servers["challenge"] = {"listen": [":80"], "protocols": ["h1"], "routes": routes, "automatic_https": {"disable": True}}
        tls["automation"] = {"policies": [{"subjects": [domain], "issuers": [{"module": "acme", "ca": "https://acme-v02.api.letsencrypt.org/directory",
                             "email": record["email"], "challenges": {"tls-alpn": {"disabled": True}}}]}]}
    else:
        servers["decoy"]["automatic_https"]["disable_certificates"] = True
        tls["certificates"] = {"load_files": [{"certificate": record["certificate"], "key": record["certificate_key"]}]}
    return {"admin": {"disabled": True}, "storage": {"module": "file_system", "root": "/data"}, "apps": {"http": {"servers": servers}, "tls": tls}}


def probe(record, cafile=None):
    try:
        context = ssl.create_default_context(cafile=cafile)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.set_alpn_protocols(["h2"])
        with socket.create_connection(("127.0.0.1", record["https_port"]), timeout=2) as connection:
            with context.wrap_socket(connection, server_hostname=record["domain"]) as secure:
                certificate = secure.getpeercert()
                if secure.selected_alpn_protocol() != "h2":
                    raise ValueError("Decoy does not negotiate HTTP/2")
                expires = ssl.cert_time_to_seconds(certificate["notAfter"])
                if expires < time.time() + 86400:
                    raise ValueError("Decoy certificate expires within 24 hours; renew it before using it")
                return {"ready": True, "status": "Ready", "error": "", "expires_at": int(expires)}
    except (OSError, ValueError, KeyError) as error:
        return {"ready": False, "status": "Waiting for trusted HTTPS", "error": str(error)}


def snapshot(namespace):
    from xray_support import _read, _running
    saved = _read(os.path.join(namespace["STATE_DIR"], "xray", "decoy", "state.json"))
    if not saved:
        return {}
    running = _running(namespace, CONTAINER)
    report = {key: saved.get(key) for key in ("domain", "mode", "https_port", "http_port", "error", "status")}
    report.update(running=running, ready=False)
    if running and not saved.get("error"):
        report.update(probe(saved, namespace.get("XRAY_SELFHOST_CA_FILE")))
    elif not running and not report.get("error"):
        report.update(status="Stopped", error="Decoy container is not running. Use Retry setup.")
    return report


def reconcile(record, remove, namespace):
    from xray_support import _docker, _read, _write, _running, host_node_dir
    directory = os.path.join(namespace["STATE_DIR"], "xray", "decoy")
    state_path = os.path.join(directory, "state.json")
    previous = _read(state_path)
    if remove and not record:
        _docker(namespace, ["rm", "-f", CONTAINER])
        with contextlib.suppress(OSError):
            os.unlink(state_path)
        return
    if not record:
        return
    try:
        record = validate(record)
        signature = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        if signature == previous.get("signature") and _running(namespace, CONTAINER):
            return
        if previous.get("attempted") == signature and time.time() - previous.get("attempted_at", 0) < 60:
            return
        same = _running(namespace, CONTAINER)
        for field in ("http_port", "https_port"):
            port = record.get(field)
            if not port or (same and port == previous.get(field)):
                continue
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("0.0.0.0", port))
        if record["mode"] == "acme":
            result = dns_check(record["domain"], namespace.get("ENDPOINT_HOST") or namespace.get("NODE_ENDPOINT") or namespace.get("ENDPOINT_IP") or "")
            if not result["ok"]:
                raise ValueError(result["message"])
        candidate = os.path.join(directory, "candidate.json")
        _write(candidate, caddy_config(record))
        host_directory = host_node_dir(namespace).rstrip("/") + "/xray/decoy"
        os.makedirs(os.path.join(directory, "data"), mode=0o700, exist_ok=True)
        mounts = ["--mount", "type=bind,src=" + host_directory + "/candidate.json,dst=/config.json,readonly",
                  "--mount", "type=bind,src=" + host_directory + "/data,dst=/data"]
        if record["mode"] == "certificate":
            for field in ("certificate", "certificate_key"):
                path = record[field]
                source = host_node_dir(namespace) + path[len(namespace["STATE_DIR"]):] if path.startswith(namespace["STATE_DIR"].rstrip("/") + "/") else path
                mounts += ["--mount", "type=bind,src=" + source + ",dst=" + path + ",readonly"]
        test = _docker(namespace, ["run", "--rm", "--network", "none", "--user", "0:0"] + mounts + [CADDY_IMAGE, "caddy", "validate", "--config", "/config.json"], timeout=180)
        if test[0]:
            raise ValueError((test[2] or test[1])[-1600:])
        os.replace(candidate, os.path.join(directory, "config.json"))
        mounts[1] = "type=bind,src=" + host_directory + "/config.json,dst=/config.json,readonly"
        _docker(namespace, ["rm", "-f", CONTAINER])
        start = _docker(namespace, ["run", "-d", "--name", CONTAINER, "--network", "host", "--restart", "unless-stopped", "--user", "0:0",
                                    "--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE", "--security-opt", "no-new-privileges", "--read-only",
                                    "--log-opt", "max-size=5m", "--log-opt", "max-file=2"] + mounts + [CADDY_IMAGE, "caddy", "run", "--config", "/config.json"])
        if start[0]:
            raise ValueError((start[2] or start[1])[-1600:])
        _write(state_path, dict(record, signature=signature, status="Provisioning HTTPS", error=""))
    except (ValueError, TypeError, OSError, KeyError, subprocess.SubprocessError) as error:
        _write(state_path, dict(previous or record, error=str(error), status="Setup failed", attempted=locals().get("signature"), attempted_at=time.time()))
