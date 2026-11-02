import "./spa_env.mjs";
import assert from "node:assert/strict";
const { installationCommands, bootCmd, shellQuote, BOOTSTRAP_URL } = await import("../js/install.js");

assert.ok(BOOTSTRAP_URL.includes("dainickzan/swg-panel/main/bootstrap.sh"));
assert.ok(bootCmd("update").includes(BOOTSTRAP_URL));
assert.equal(shellQuote("a'b;$HOME"), "'a'\\''b;$HOME'");
for (const method of ["docker", "baremetal"]) {
  for (const role of ["master", "host", "node"]) {
    const result = installationCommands({ role, method, domain: "panel.example.org", endpoint: "203.0.113.10", name: "edge's node",
      panelUrl: "https://panel.example.org/swg", token: "TEST-TOKEN", username: "admin", tls: "selfsigned" });
    assert.equal(result.preview, result.install + " --dry-run");
    assert.ok(result.install.startsWith("sudo SWG_REPO='https://github.com/dainickzan/swg-panel' SWG_REF='main'"));
    assert.ok(result.install.includes(" -- " + (method === "docker" ? "docker " : "") + role));
    assert.equal(result.install.includes("--build"), method === "docker");
    if (role === "node") {
      assert.ok(result.install.includes("NODE_TOKEN='TEST-TOKEN'"));
      assert.ok(result.install.includes("PANEL_URL='https://panel.example.org/swg'"));
      assert.ok(result.install.includes("'edge'\\''s node'"));
      assert.ok(!result.install.includes("ROLE="));
    } else {
      assert.ok(!result.install.includes("NODE_TOKEN="));
      assert.ok(result.install.includes("PANEL_DOMAIN='panel.example.org'"));
      assert.ok(!result.install.includes("PASS="));
      assert.equal(result.install.includes("203.0.113.10"), role === "master");
    }
  }
}
assert.throws(() => installationCommands({ role: "invalid", method: "docker" }));
console.log("PASS: all six install commands use fork, correct role/env, safe quoting and preview");
