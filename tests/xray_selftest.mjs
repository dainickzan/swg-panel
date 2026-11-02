import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const source = fs.readFileSync(new URL("../reconcile.js", import.meta.url), "utf8");
const context = { console, module: { exports: {} } };
vm.createContext(context);
vm.runInContext(source.replace(/^import .*;$/m, "const T = text => text;").replace("export { reconcile, DEFAULTS };", "module.exports = { reconcile, DEFAULTS };"), context);
const engine = context.module.exports;
const roster = { users: {}, peers: { test: { id: "test", title: "test", xray_uuid: "secret", created_at: 10,
  targets: [{ node: "node", iface: "xray1", type: "xray" }] } } };
const stats = { node: { generated_at: 100, xray: [{ iface: "xray1", running: true,
  peers: [{ peer_id: "test", online: true, rx_bytes: 100, tx_bytes: 200, rx_rate: 10, tx_rate: 20 }] }] } };
const result = engine.reconcile(roster, stats, 100000, { nodeSeen: { node: 100 } });
assert.equal(result.peers[0].xray_uuid, "secret");
assert.equal(result.peers[0].targets[0].type, "xray");
assert.equal(result.peers[0].targets[0].kwXfer.rx_bytes, 100);
console.log("Xray reconcile telemetry: PASS");
const storeSource = fs.readFileSync(new URL("../js/store.js", import.meta.url), "utf8");
const method = storeSource.match(/userIfacesOf\(node\)\s*\{([\s\S]*?)\n  \},/)[1];
const reader = new Function("node", method);
const isolated = { nodes: [{ id: "node", xray_cfg: { xray1: {} } }], fleet: [{ id: "node", name: "test" }], stats: {},
  ifacesOf: () => [], ifaceIsSystem: () => false, node: () => ({ id: "node", name: "test" }) };
assert.deepEqual(reader.call(isolated, "node"), ["xray1"]);
console.log("Xray-only roster filters: PASS");
