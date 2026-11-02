import "./spa_env.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
const { Store } = await import("../js/store.js");
const { XraySetupActions } = await import("../js/xray.js");

function buttons(vnode) {
  if (!vnode) return [];
  if (Array.isArray(vnode)) return vnode.flatMap(buttons);
  if (typeof vnode !== "object") return [];
  return vnode.type === "button" ? [vnode] : buttons(vnode.props?.children);
}

for (const capable of [true, false]) {
  Store.nodes = [{ id: "node", xray_capable: capable, xray_selfhost_capable: capable }];
  for (const chips of [true, false]) {
    const actions = buttons(XraySetupActions({ node: "node", chips }));
    assert.equal(actions.length, 2);
    assert.equal(actions[0].props.children, "Xray");
    assert.equal(actions[0].props.disabled, false);
    assert.equal(actions[0].props.title.includes("preserving its enrollment token"), !capable);
    assert.equal(actions[1].props.title.includes("preserving its enrollment token"), !capable);
    assert.ok(buttons(XraySetupActions({ node: "node", blocked: true, chips })).every(action => action.props.disabled));
  }
}
const nodeSource = fs.readFileSync(new URL("../js/screen-nodes.js", import.meta.url), "utf8");
assert.ok(nodeSource.includes("<${XraySetupActions} node=${name} blocked=${blocked}/>") );
const ifaceSource = fs.readFileSync(new URL("../js/iface.js", import.meta.url), "utf8");
assert.ok(ifaceSource.includes("<${XraySetupActions} node=${node} chips/>") );
console.log("PASS: Xray and self-hosted setup stay visible; unsupported nodes explain fork update; blocked nodes stay disabled");
