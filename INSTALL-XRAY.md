# Install the Xray-enabled fork

Repository: `dainickzan/swg-panel`, forked from `SanityProtocol/swg-panel`.
The original role/method selection, enrollment, recovery and uninstall workflows remain.
Our panel includes Xray interfaces and the self-hosted REALITY HTTPS wizard.

## Quick commands

Run as root or use `sudo` as shown. Start with the dry-run preview on a fresh Linux
server; remove `--dry-run` to perform the installation. Configuration questions
remain interactive unless their documented environment variables are supplied.

```sh
# Master: new panel and auto-enrolled entry node
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/dainickzan/swg-panel/main/bootstrap.sh)" -- docker master --build --dry-run

# Host/server: new panel only; entry nodes are installed separately
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/dainickzan/swg-panel/main/bootstrap.sh)" -- docker host --build --dry-run

# Node: get its command/token from your running panel, never invent/re-mint it
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/dainickzan/swg-panel/main/bootstrap.sh)" -- docker node --build --dry-run
```

In the panel open **Nodes -> Setup commands**. Select master, host or node and
Docker or bare-metal. The sheet provides **Preview** and **Install** commands.
For a new node, **Create node + enrollment command** mints its real token through
the existing API. For an existing node use its normal token/recovery dialog instead.
Passwords are requested on the server, not placed in the generated commands.
Treat node commands as credentials; do not paste them into public logs or tickets.

Docker defaults to building the checked-out fork on the target server. This works
before public GHCR images exist and cannot silently run upstream code. Build time
depends on the server; the node image compiles AmneziaWG tooling. Published fork
images can be selected explicitly with `BUILD=false`, after confirming their
availability at `ghcr.io/dainickzan/swg-panel` and `ghcr.io/dainickzan/swg-node`.

Bare-metal commands omit `docker` and `--build`: use `master`, `host`, or `node`.
WG/AWG remains native. Masters/nodes install Docker for the isolated Xray/Caddy
containers on apt-based systems. On other distributions install/start Docker
yourself. `XRAY_RUNTIME=off` skips Docker installation; Xray controls remain
unavailable until a working Docker socket is present. Native NixOS installations
must declare their container runtime separately; packaging includes the modules.

## Updates and source selection

The bootstrap default, panel enrollment commands and update wrappers point to
this fork. `SWG_REPO` and `SWG_REF` remain supported; a supplied GitHub raw
`SWG_BOOTSTRAP_URL` also selects its repository. A locally built Docker install
keeps its build blocks and unique image tag, and the normal updater rebuilds from
the fork rather than pulling the original project.

```sh
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/dainickzan/swg-panel/main/bootstrap.sh)" -- update -y --no-components
```

The customized VPS deployment still has its previous safe local image override
and disabled automatic extension updates. Do not replace its Compose files or
re-enroll its node with the fresh-install commands. Follow `XRAY.md` for its
deployment/rollback procedure.

After installation, configure Xray from **Nodes -> your node -> Create new
interface** or **Set up steal oneself**. The self-hosted wizard requires an owned
public domain, correct DNS, and either free/reachable TCP80 for HTTP-01 or an
existing trusted certificate. Panel443 remains unchanged; public Xray defaults
to TCP10443. Provider firewalls are configured separately.
