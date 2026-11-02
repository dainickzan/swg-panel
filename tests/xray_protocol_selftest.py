import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xray_support as xray


def execute(arguments):
    return subprocess.run(arguments, text=True, capture_output=True, timeout=60)


def docker(arguments):
    result = execute(["docker"] + (arguments[:1] + ["--user", "0:0"] + arguments[1:] if arguments[0] == "run" else arguments))
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout)[-1800:])
    return result.stdout


directory = tempfile.mkdtemp(prefix="swg-xray-protocol-", dir="/tmp")
certificate = "/var/lib/swg-noded/xray/tls/test.pem"
key = "/var/lib/swg-noded/xray/tls/test.key"
result = execute(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                  "-addext", "subjectAltName=DNS:localhost", "-keyout", directory + "/test.key", "-out", directory + "/test.pem"])
assert not result.returncode
mounts = ["--mount", "type=bind,src=" + directory + ",dst=/var/lib/swg-noded/xray/tls,readonly"]
server, client = "swg-xray-protocol-server", "swg-xray-protocol-client"
credential = xray.new_credential()
try:
    modes = [(protocol, transport, "chacha20-ietf-poly1305") for protocol in ("vless", "vmess", "trojan") for transport in ("tcp", "ws")]
    modes += [("shadowsocks", "tcp", method) for method in ("chacha20-ietf-poly1305", "aes-128-gcm", "aes-256-gcm")]
    for protocol, transport, method in modes:
        record = xray.validate({"protocol": protocol, "transport": transport, "security": "none" if protocol == "shadowsocks" else "tls",
                                "flow": "", "server_name": "localhost", "endpoint_host": "127.0.0.1", "listen": "127.0.0.1", "port": 59446,
                                "certificate": certificate, "certificate_key": key, "path": "/test", "method": method}, {}, {})
        record.update(api_port=61997, clients=[{"id": credential, "email": "protocol-test@swg"}])
        for clients in ([], record["clients"]):
            config = xray.server_config(dict(record, clients=clients))
            xray._write(directory + "/server.json", config)
            docker(["run", "--rm", "--network", "none"] + mounts + [xray.XRAY_IMAGE, "run", "-test", "-config", "/var/lib/swg-noded/xray/tls/server.json"])
        if protocol in ("vless", "vmess"):
            user = {"id": credential, "encryption": "none"} if protocol == "vless" else {"id": credential, "security": "auto"}
            settings = {"vnext": [{"address": "127.0.0.1", "port": 59446, "users": [user]}]}
        else:
            remote = {"address": "127.0.0.1", "port": 59446, "password": credential}
            if protocol == "shadowsocks":
                remote["method"] = method
            settings = {"servers": [remote]}
        stream = {"network": transport, "security": record["security"]}
        if protocol != "shadowsocks":
            stream["tlsSettings"] = {"serverName": "localhost", "certificates": [{"certificateFile": certificate, "usage": "verify"}]}
        if transport == "ws":
            stream["wsSettings"] = {"path": "/test"}
        config = {"log": {"loglevel": "warning"}, "inbounds": [{"listen": "127.0.0.1", "port": 59447, "protocol": "socks"}],
                  "outbounds": [{"protocol": protocol, "settings": settings, "streamSettings": stream}]}
        xray._write(directory + "/client.json", config)
        for name, filename in ((server, "server.json"), (client, "client.json")):
            docker(["run", "-d", "--name", name, "--network", "host"] + mounts + [xray.XRAY_IMAGE, "run", "-config", "/var/lib/swg-noded/xray/tls/" + filename])
        time.sleep(1)
        request = execute(["curl", "--fail", "--silent", "--socks5-hostname", "127.0.0.1:59447", "--max-time", "20", "https://example.com"])
        if request.returncode:
            raise RuntimeError(protocol + "/" + transport + ": " + request.stderr + docker(["logs", server])[-1000:] + docker(["logs", client])[-1000:])
        assert "Example Domain" in request.stdout
        print("PASS: real HTTPS traffic over " + protocol + "/" + transport + "/" + method, flush=True)
        for name in (server, client):
            docker(["rm", "-f", name])
    for egress in ("socks", "http"):
        record = xray.validate({"egress_type": egress, "egress_host": "127.0.0.1", "egress_port": 1080, "egress_user": "test", "egress_password": "test",
                                "blocked_domains": ["example.org"], "blocked_ips": ["10.0.0.0/8"], "port": 59446, "endpoint_host": "127.0.0.1"}, {}, {})
        record.update(api_port=61997, clients=[])
        xray._write(directory + "/server.json", xray.server_config(record))
        docker(["run", "--rm", "--network", "none"] + mounts + [xray.XRAY_IMAGE, "run", "-test", "-config", "/var/lib/swg-noded/xray/tls/server.json"])
        print("PASS: routing blocks and authenticated " + egress + " egress validate", flush=True)
finally:
    for name in (server, client):
        execute(["docker", "rm", "-f", name])
