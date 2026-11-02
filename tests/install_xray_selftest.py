import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import xray_support


class InstallTests(unittest.TestCase):
    def test_shell_syntax(self):
        for name in ("bootstrap.sh", "install-docker.sh", "install-host.sh", "install-node.sh", "update.sh", "lib/common.sh"):
            with self.subTest(name=name):
                subprocess.run(["bash", "-n", str(ROOT / name)], check=True)

    def test_all_delivery_paths_include_python_modules(self):
        for name in ("Dockerfile", "Dockerfile.node", "install-docker.sh", "install-host.sh", "install-node.sh", "update.sh", "nix/package.nix"):
            source = (ROOT / name).read_text()
            for module in ("xray_support.py", "xray_selfhost.py"):
                with self.subTest(name=name, module=module):
                    self.assertIn(module, source)
        self.assertIn('BUILD="${BUILD:-true}"', (ROOT / "install-docker.sh").read_text())
        self.assertIn("/dev/null:/var/lib/swg-panel/xray-keys.json:ro", (ROOT / "docker-compose.yml").read_text())

    def test_source_stays_forked(self):
        for name in ("bootstrap.sh", "lib/common.sh", "swg-panel-server", "swg-noded", "js/install.js", "docker-compose.yml"):
            with self.subTest(name=name):
                self.assertIn("dainickzan/swg-panel" if name != "docker-compose.yml" else "ghcr.io/dainickzan/swg-panel", (ROOT / name).read_text())

    def test_native_node_uses_host_state_not_container_path(self):
        self.assertEqual(xray_support.host_node_dir({"NODE_KIND": "baremetal", "STATE_DIR": "/var/lib/swg-noded"}), "/var/lib/swg-noded")
        self.assertEqual(xray_support.host_node_dir({"NODE_KIND": "docker", "STATE_DIR": "/var/lib/swg-noded"}), "")
        self.assertEqual(xray_support.host_node_dir({"NODE_KIND": "docker", "STATE_DIR": "/var/lib/swg-noded", "SWG_HOST_NODE_DIR": "/opt/panel/data/node"}), "/opt/panel/data/node")

    def test_update_wrapper_keeps_custom_repo(self):
        result = subprocess.run(["bash", "-c", 'source "$1/lib/common.sh"; swg_update_wrapper_text main', "bash", str(ROOT)],
                                env=dict(os.environ, SWG_REPO="https://github.com/example/custom-panel.git"), capture_output=True, text=True, check=True)
        self.assertIn("raw.githubusercontent.com/example/custom-panel/main/bootstrap.sh", result.stdout)

    def test_image_tag_migration_preserves_repository(self):
        source = (ROOT / "update.sh").read_text().split("<<'PYTAG'", 1)[1].split("\nPYTAG", 1)[0]
        for owner in ("sanityprotocol", "dainickzan"):
            with self.subTest(owner=owner), tempfile.TemporaryDirectory() as directory:
                compose = pathlib.Path(directory) / "docker-compose.yml"
                environment = pathlib.Path(directory) / ".env"
                compose.write_text('services:\n  node:\n    image: ghcr.io/' + owner + '/swg-node:sha-test\n    environment:\n      SWG_TURN_IMAGE: "${SWG_TURN_IMAGE:-ghcr.io/' + owner + '/swg-node:latest}"\n')
                subprocess.run([sys.executable, "-", str(compose), str(environment)], input=source, text=True, check=True)
                self.assertIn("image: ghcr.io/" + owner + "/swg-node:${SWG_IMAGE_TAG:-latest}", compose.read_text())
                self.assertIn("SWG_TURN_IMAGE:-ghcr.io/" + owner + "/swg-node:${SWG_IMAGE_TAG:-latest}", compose.read_text())
                self.assertIn("SWG_IMAGE_TAG=sha-test", environment.read_text())

    def test_uninstall_scopes_xray_cleanup(self):
        source = (ROOT / "uninstall.sh").read_text()
        self.assertIn('name=^/swg-xray-(xray[0-9]+|decoy)$', source)
        for function in ("rm_node", "rm_docker_node", "rm_docker_files"):
            body = source.split(function + "(){", 1)[1].split("\n}", 1)[0]
            self.assertIn("rm_xray_containers", body)

    def test_docker_dry_runs_stage_complete_context(self):
        for role in ("master", "host", "node"):
            with self.subTest(role=role), tempfile.TemporaryDirectory(prefix="swg-install-preview-") as directory:
                environment = dict(os.environ, SWG_DOCKER_DIR="/opt/swg-preview", PANEL_PASSWORD="test-preview-only", PANEL_DOMAIN="203.0.113.20", TLS="selfsigned",
                                   NODE_TOKEN="test-preview-token", PANEL_URL="https://panel.example.org", NODE_ENDPOINT="203.0.113.20", NODE_NAME="preview", NODE_NET="host", TLS_VERIFY="yes")
                result = subprocess.run(["bash", str(ROOT / "install-docker.sh"), role, "--build", "--dry-run"], cwd=directory, env=environment,
                                        capture_output=True, text=True, stdin=subprocess.DEVNULL, start_new_session=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout[-1800:] + result.stderr[-1800:])
                staged = pathlib.Path(directory) / "dryrun/opt/swg-preview"
                for module in ("xray_support.py", "xray_selfhost.py", "swg-relay", "js/install.js"):
                    self.assertTrue((staged / module).is_file(), module)
                config = (staged / "docker-compose.yml").read_text()
                self.assertIn("ghcr.io/dainickzan/", config)
                self.assertIn("    build:", config)
                self.assertIn("SWG_IMAGE_TAG=local", (staged / ".env").read_text())


if __name__ == "__main__":
    unittest.main()
