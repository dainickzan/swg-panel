# Xray interfaces

This local extension targets upstream `7fbf2cf` (1.8.11-beta). On a Docker-managed node,
choose **Nodes -> Create new interface -> Xray**. The default is VLESS over TCP with
REALITY and Vision; it generates and preserves an X25519 keypair and short ID.
Port 443 is already used by this panel: choose a free port such as 10443.

Supported modes:

- VLESS: TCP/REALITY, TCP/TLS, WebSocket/TLS.
- VMess and Trojan: TCP/TLS or WebSocket/TLS.
- Shadowsocks: TCP/UDP with AES-GCM or ChaCha20-Poly1305.

TLS certificate files must live under `/var/lib/swg-noded/xray/tls/` on the node
(Docker host: `data/node/xray/tls/`). Renewal is external. Never disable TLS
certificate verification in clients to accommodate a self-signed certificate.

The interface supports edit, start, stop, restart, delete, peer creation,
shared user assignment/blocking/expiry, multiple deployments, credential rotation,
QR/copy/import links, subscription-page links, traffic counters and domain/IP blocks.
Egress is direct, SOCKS5 or HTTP proxy. Statistics APIs bind only to loopback.

Xray is a proxy, not an IP tunnel. WireGuard subnets, mesh exits, LAN sharing,
WireGuard keys and force-DNS rules do not apply. Changes to the client set briefly
restart only its Xray container. Existing VPN interfaces are not reconfigured.
Online indicates traffic within the last two minutes, not an idle session.
The node tests each candidate with the pinned Xray binary before replacement.

The extension images are built from the installed upstream images using
`Dockerfile.xray-panel` and `Dockerfile.xray-node`. The deployment override must
mask `/var/lib/swg-panel/xray-keys.json` with `/dev/null:ro` on `swg-sub`.
Do not use the upstream one-click updater on this custom build: it replaces the
extension. Keep the override and rebuild/retest against future upstream versions.

Tests: `python3 tests/xray_selftest.py` and `node tests/xray_selftest.mjs`.

## Quick self-hosted REALITY setup

Open **Nodes -> your node -> User interfaces -> Set up steal oneself**, or choose
**Create new interface -> Xray · steal oneself**. Existing REALITY interfaces have
the same setup/manage button. One shared, managed Caddy decoy is supported per node.

1. Enter a public domain you own. Point all its A/AAAA records directly to this
   node's endpoint; disable CDN proxying and remove incorrect IPv6 records.
2. Click **Check DNS**, then enter a certificate contact email and click
   **Set up decoy + certificate**. TCP port 80 must be free and externally reachable
   for Let's Encrypt HTTP-01 issuance and automatic renewal. Port 443 is never used
   for certificate challenges and the existing panel is not moved.
3. Wait for **Ready**, then click **Create self-hosted Xray** or **Use self-hosted
   target**. Readiness requires trusted TLS 1.3, HTTP/2 and the correct hostname.
   The local HTTPS decoy defaults to 127.0.0.1:4443; the public Xray port defaults
   to 10443. Allow the public TCP port in your provider firewall.
4. Create peers as usual and distribute their links/QR codes. Changing an existing
   interface's SNI changes its client links, but preserves its REALITY private key.

If port 80 is unavailable, select **Use existing certificate on node** and provide
a publicly trusted certificate/key under `/var/lib/swg-noded/xray/tls/` (Docker host:
`/opt/swg-panel-docker/data/node/xray/tls/`). Renewal in this mode is external; click
**Retry setup** after replacing the files. Self-signed certificates are refused.

Setup refuses occupied ports rather than stopping other services. To change or
remove a decoy used by interfaces, first switch all of them to an external target.
**Remove decoy** stops only `swg-xray-decoy`; its certificate data is retained.
No domain, decoy or certificate is provisioned merely by deploying this feature.

The self-host wizard is deployed on this VPS. Its separate private rollback backup
is `/opt/swg-panel-backups/before-selfhost-20261102T113018Z/`, including the previous
Xray-enabled image IDs, Compose files and data archive. To roll back only this
wizard's code, keep the current override and add that backup's `rollback.yml` as
the last Compose file. Do not restore old data over the running panel.

Additional tests: `python3 tests/xray_selfhost_selftest.py` and
`python3 tests/xray_selfhost_live_selftest.py` (isolated Docker integration tests).

## Installed on this VPS

Source: `/opt/swg-panel-xray`. Compose deployment: `/opt/swg-panel-docker`.
The active `docker-compose.override.yml` selects the custom images and disables only
the upstream self-update triggers. The system update timer and network-control services
remain enabled. Existing users, peers, node token hashes and interface settings were
compared with their pre-deployment values and preserved.

Private pre-deployment backup:
`/opt/swg-panel-backups/before-xray-20261102T010257Z/`.
It contains `data.tar.gz`, the original Compose/environment files and `rollback.yml`
with the exact original image IDs. Original images also have local `:before-xray` tags.
Do not publish this backup: it contains credentials and private keys.

To rebuild this extension after editing its source:

```sh
cd /opt/swg-panel-xray
docker build -f Dockerfile.xray-panel -t swg-panel-xray:1.8.11-local .
docker build -f Dockerfile.xray-node -t swg-node-xray:1.8.11-local .
cd /opt/swg-panel-docker
docker compose --profile master config --quiet
docker compose --profile master up -d --no-deps swg-panel swg-sub swg-node
```

To roll back, first delete all Xray interfaces through the panel and wait for their
containers to disappear. Then run on the VPS:

```sh
cd /opt/swg-panel-docker
mv docker-compose.override.yml docker-compose.xray.disabled.yml
docker compose -f docker-compose.yml -f /opt/swg-panel-backups/before-xray-20261102T010257Z/rollback.yml --profile master up -d swg-panel swg-sub swg-node
```

This rolls back code, not user data. Do not extract the old data archive over a running
panel or overwrite newer roster changes. Keep the original-image override if subsequent
Compose invocations must remain pinned to these exact originals.

Validation includes isolated API/unit tests, roster/blocking and snapshot regressions,
real HTTPS traffic for REALITY/Vision and every advertised TLS/TCP, TLS/WebSocket and
Shadowsocks encryption combination, plus production API-to-node traffic, credential
revocation, user/peer blocking, telemetry and stop/start. Temporary production test
interfaces, peers and users were deleted through the API.

Browser checks cover creation, editing, peer QR, rotation, deletion, roster selection,
subscription QR/link rendering and a 390-pixel mobile viewport. Tests live in `tests/`
and are included in the fork and deployment source archive.
The provider's external firewall must allow the chosen public TCP port (and UDP for
Shadowsocks). No provider firewall was modified. No example public Xray interface is
left enabled: create your own interface and peers in the panel.
