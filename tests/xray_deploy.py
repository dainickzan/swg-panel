import datetime
import json
import os
import pathlib
import shutil
import subprocess
import time
import urllib.request

root = pathlib.Path("/opt/swg-panel-docker")
source = pathlib.Path("/opt/swg-panel-xray")
backup = pathlib.Path("/opt/swg-panel-backups") / ("before-xray-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
os.umask(0o077)
backup.mkdir(parents=True, mode=0o700)
override = root / "docker-compose.override.yml"
if override.exists():
    raise RuntimeError("An existing override must be merged rather than replaced")


def run(arguments):
    subprocess.run(arguments, cwd=root, check=True)


def health():
    for attempt in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8088/healthz", timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("Panel did not become healthy")


run(["python3", str(source / "tests/xray_deployment_check.py"), "baseline"])
for filename in ("docker-compose.yml", ".env"):
    shutil.copy2(root / filename, backup / filename)
run(["tar", "-czf", str(backup / "data.tar.gz"), "data"])
key_path = root / "data/lib/xray-keys.json"
if not key_path.exists():
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump({}, handle)
images = {}
for service in ("swg-panel", "swg-sub", "swg-node"):
    record = json.loads(subprocess.check_output(["docker", "inspect", service]))[0]
    images[service] = record["Image"]
    subprocess.run(["docker", "tag", record["Image"], service + ":before-xray"], check=True)
(backup / "original-images.json").write_text(json.dumps(images))
rollback = "services:\n"
for service, image in images.items():
    rollback += "  " + service + ":\n    image: " + image + "\n    pull_policy: never\n"
(backup / "rollback.yml").write_text(rollback)
run(["docker", "compose", "-f", "docker-compose.yml", "-f", str(source / "docker-compose.xray.yml"), "--profile", "master", "config", "--quiet"])
shutil.copy2(source / "docker-compose.xray.yml", override)
run(["docker", "compose", "--profile", "master", "config", "--quiet"])
(source / "deployment.json").write_text(json.dumps({"backup": str(backup), "root": str(root), "original_images": images}))
print("Backup complete: " + str(backup), flush=True)
run(["docker", "compose", "--profile", "master", "up", "-d", "--no-deps", "swg-panel"])
health()
run(["docker", "compose", "--profile", "master", "up", "-d", "--no-deps", "swg-sub"])
run(["docker", "compose", "--profile", "master", "up", "-d", "--no-deps", "swg-node"])
health()
print("Extension deployed; waiting for node sync", flush=True)
