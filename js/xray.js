import { T, srvText } from "./i18n.js";
import { Store, api, useStore } from "./store.js";
import { fmtBytes } from "./util.js";
import { Ic, Panel, Sheet, footRow, toast, copy, openModal, closeModal, openConfirm } from "./ui.js";
import { QR } from "./crypto.js";
import { h } from "preact";
import { useState, useEffect } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);
const nodeRecord = node => (Store.nodes || []).find(record => record.id === node) || {};

export function openXray(node, iface) {
  openModal(html`<${XraySheet} node=${node} iface=${iface}/>`);
}

export function openSelfHostedXray(node, iface) {
  openModal(html`<${SelfHostedXraySheet} node=${node} iface=${iface}/>`);
}

export function SelfHostedXraySheet({ node, iface }) {
  useStore();
  const record = nodeRecord(node);
  const existing = record.xray_selfhost_cfg || {};
  const report = (Store.stats[node] || {}).xray_selfhost || {};
  const [domain, setDomain] = useState(existing.domain || "");
  const [email, setEmail] = useState(existing.email || "");
  const [mode, setMode] = useState(existing.mode || "acme");
  const [httpsPort, setHttpsPort] = useState(String(existing.https_port || 4443));
  const [certificate, setCertificate] = useState(existing.certificate || "");
  const [certificateKey, setCertificateKey] = useState(existing.certificate_key || "");
  const [interfaceName, setInterfaceName] = useState(iface || (() => { let index = 1; while ((record.xray_cfg || {})["xray" + index]) index++; return "xray" + index; })());
  const [port, setPort] = useState(String(((record.xray_cfg || {})[iface] || {}).port || 10443));
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dns, setDns] = useState(null);
  const settings = { node, domain: domain.trim(), mode, email: email.trim(), https_port: Number(httpsPort), certificate, certificate_key: certificateKey };
  const changed = domain.trim().toLowerCase() !== (existing.domain || "") || mode !== (existing.mode || "acme") || Number(httpsPort) !== existing.https_port ||
    (mode === "acme" ? email.trim() !== (existing.email || "") : certificate !== existing.certificate || certificateKey !== existing.certificate_key);
  const ready = !!report.ready && !changed && report.domain === existing.domain && report.https_port === existing.https_port;
  const request = async (path, body, onSuccess) => {
    setBusy(true); setError("");
    try {
      const response = await api.post(path, body);
      if (!response.ok) { setError(srvText(response) || T("Setup failed")); if (response.data) setDns(response.data); return; }
      if (onSuccess) await onSuccess(response.data);
      await Store.poll();
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  const apply = () => request("/api/xray/set", { ...((record.xray_cfg || {})[iface] || {}), node, iface: interfaceName, port: Number(port),
    endpoint_host: record.endpoint_host, protocol: "vless", security: "reality", transport: "tcp", flow: "xtls-rprx-vision", selfhost: true,
    server_name: existing.domain, target: "127.0.0.1:" + existing.https_port }, () => {
      closeModal(); location.hash = "#/node/" + encodeURIComponent(node) + "/" + encodeURIComponent(interfaceName);
      toast(T("Saved — applies on the node's next sync."), "ok");
    });
  const field = (label, value, setter, type = "text", disabled = false) => html`<div class="field"><label>${T(label)}</label><input type=${type} value=${value} disabled=${busy || disabled} onInput=${event => { setter(event.target.value); setDns(null); }}/></div>`;
  return html`<${Sheet} title=${T("Self-hosted REALITY · steal oneself")} width=${720} err=${error}
    foot=${footRow({ onCancel: closeModal, disabled: busy || !ready || (!!iface && !accepted), onAction: apply, action: T(iface ? "Use self-hosted target" : "Create self-hosted Xray") })}>
    <div class="hint">${T("A real HTTPS decoy runs on this node. No third-party target is used. Panel ports and addresses stay unchanged.")}</div>
    ${field("Domain you control", domain, setDomain)}
    <div class="hint">${T("Point all A/AAAA records directly to this node. Disable CDN proxying. A valid public domain and trusted certificate are required.")}</div>
    <div class="field"><label>${T("Certificate")}</label><select value=${mode} disabled=${busy} onChange=${event => setMode(event.target.value)}>
      <option value="acme">${T("Automatic Let's Encrypt (port 80)")}</option><option value="certificate">${T("Use existing certificate on node")}</option></select></div>
    ${mode === "acme" ? html`${field("Certificate contact email", email, setEmail, "email")}<div class="hint">${T("TCP port 80 must be free and reachable for issuance and renewal. No existing service is stopped. Certificates renew automatically.")}</div>`
      : html`${field("Certificate path on node", certificate, setCertificate)}${field("Certificate key path on node", certificateKey, setCertificateKey)}<div class="hint">${T("Place trusted certificates under /var/lib/swg-noded/xray/tls/. Renewal is external; use Retry setup after replacing certificate files. Self-signed certificates are refused.")}</div>`}
    ${field("Local decoy HTTPS port", httpsPort, setHttpsPort, "number")}
    <div class="hint">${T("Decoy HTTPS binds only to 127.0.0.1. The public panel on port 443 is not moved.")}</div>
    <div class="chiprow" style="flex-wrap:wrap"><button class="btn btn-mini" disabled=${busy || !domain.trim()} onClick=${() => request("/api/xray/selfhost/check", settings, setDns)}>${T("Check DNS")}</button>
      <button class="btn btn-mini" disabled=${busy || !domain.trim()} onClick=${() => request("/api/xray/selfhost/setup", settings)}>${T("Set up decoy + certificate")}</button>
      ${existing.domain ? html`<button class="btn btn-mini" disabled=${busy} onClick=${() => request("/api/xray/selfhost/retry", { node })}>${T("Retry setup")}</button>` : null}</div>
    ${dns ? html`<div style="display:block;overflow-wrap:anywhere" class=${"notice " + (dns.ok ? "" : "danger")}>${dns.message}<br/>${T("Resolved IPs")}: ${(dns.addresses || []).join(", ") || "—"}<br/>${T("Node IPs")}: ${(dns.expected || []).join(", ") || "—"}</div>` : null}
    ${existing.domain ? html`<div style="display:block;overflow-wrap:anywhere" class=${"notice " + (report.error && !report.running ? "danger" : "")}><b>${T("Decoy")}: ${existing.domain}</b><br/>${T(report.status || "Waiting for node sync")}
      ${report.error ? html`<div>${report.error}</div>` : null}</div>` : null}
    <div class="hint">${T("The Create/Use button unlocks only after TLS 1.3, HTTP/2, hostname and certificate trust checks pass on the node.")}</div>
    <div class="row2">${field("Xray interface", interfaceName, setInterfaceName, "text", !!iface)}${field("Public Xray port", port, setPort, "number", !!iface)}</div>
    <div class="hint">${T("Allow the public Xray TCP port in your provider firewall. Port 443 is already used by the panel; 10443 is the default.")}</div>
    ${iface ? html`<div style="margin:16px 0"><label class="hint" style="display:flex;gap:8px;align-items:flex-start"><input type="checkbox" style="width:auto;flex:none;margin:4px 0" checked=${accepted} onChange=${event => setAccepted(event.target.checked)}/>${T("Changing the SNI/target changes client links. I will send users new QR codes; the REALITY key stays unchanged.")}</label></div>` : null}
    ${existing.domain ? html`<button class="btn btn-mini danger" disabled=${busy || Object.values(record.xray_cfg || {}).some(config => config.selfhost)} onClick=${() => openConfirm({
      title: T("Remove self-hosted decoy"), body: T("Stops only the managed decoy. Certificate data is retained. No Xray interface may still use it."), danger: true, confirmLabel: T("Delete"),
      onConfirm: () => request("/api/xray/selfhost/delete", { node }) })}>${T("Remove decoy")}</button>` : null}
  <//>`;
}

export function XraySheet({ node, iface }) {
  const record = nodeRecord(node);
  const existing = (record.xray_cfg || {})[iface];
  const [name, setName] = useState(iface || (() => { let index = 1; while ((record.xray_cfg || {})["xray" + index]) index++; return "xray" + index; })());
  const [form, setForm] = useState(existing || { protocol: "vless", security: "reality", transport: "tcp", port: 10443, listen: "0.0.0.0",
    endpoint_host: record.endpoint_host || "", server_name: "www.microsoft.com", target: "www.microsoft.com:443", flow: "xtls-rprx-vision",
    path: "/", method: "chacha20-ietf-poly1305", egress_type: "direct", blocked_domains: [], blocked_ips: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const change = (key, value) => setForm(current => ({ ...current, [key]: value }));
  const field = (key, label, type = "text") => html`<div class="field"><label>${T(label)}</label><input type=${type} value=${form[key] == null ? "" : form[key]}
    onInput=${event => change(key, event.target.value)}/></div>`;
  const select = (key, label, values) => html`<div class="field"><label>${T(label)}</label><select value=${form[key]} onChange=${event => {
    const value = event.target.value;
    if (key === "protocol") setForm(current => ({ ...current, protocol: value, security: value === "vless" ? "reality" : value === "shadowsocks" ? "none" : "tls",
      flow: value === "vless" ? "xtls-rprx-vision" : "", transport: "tcp" }));
    else if (key === "security") setForm(current => ({ ...current, security: value, flow: value === "reality" ? "xtls-rprx-vision" : "" }));
    else change(key, value);
  }}>${values.map(value => html`<option value=${value}>${value}</option>`)}</select></div>`;
  const save = async () => {
    setBusy(true); setError("");
    try {
      const response = await api.post("/api/xray/set", { ...form, node, iface: name, port: Number(form.port) });
      if (!response.ok) return setError(srvText(response) || T("Save failed"));
      closeModal(); await Store.poll(); location.hash = "#/node/" + encodeURIComponent(node) + "/" + encodeURIComponent(name);
      toast(T("Saved — applies on the node's next sync."), "ok");
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  return html`<${Sheet} title=${existing ? T("Edit Xray interface") + " · " + name : T("Create Xray interface")} width=${720} err=${error}
    foot=${footRow({ onCancel: closeModal, onAction: save, disabled: busy, action: T("Save") })}>
    <div class="row2"><div class="field"><label>${T("Interface name")}</label><input value=${name} disabled=${!!existing} onInput=${event => setName(event.target.value)}/></div>${field("title", "Title")}</div>
    <div class="row2">${select("protocol", "Protocol", ["vless", "vmess", "trojan", "shadowsocks"])}${select("security", "Security", form.protocol === "vless" ? ["reality", "tls"] : form.protocol === "shadowsocks" ? ["none"] : ["tls"])}</div>
    <div class="row2">${field("listen", "Listen IP")}${field("port", "Listen port", "number")}</div>
    ${field("endpoint_host", "Public endpoint IP / hostname")}
    ${form.protocol !== "shadowsocks" ? html`<div class="row2">${field("server_name", "TLS server name (SNI)")}${select("transport", "Transport", form.security === "reality" ? ["tcp"] : ["tcp", "ws"])}</div>` : select("method", "Encryption", ["chacha20-ietf-poly1305", "aes-128-gcm", "aes-256-gcm"])}
    ${form.security === "reality" ? html`${field("target", "REALITY target host:port")}<div class="hint">${T("REALITY keys are generated automatically and preserved when editing. The target must support TLS 1.3.")}</div>
      ${record.xray_selfhost_capable ? html`<button class="btn btn-mini" disabled=${busy} onClick=${() => openSelfHostedXray(node, iface)}>${T("Set up steal oneself")}</button>` : null}
      ${form.selfhost ? html`<button class="btn btn-mini" disabled=${busy} onClick=${() => setForm(current => ({ ...current, selfhost: false, target: "www.microsoft.com:443", server_name: "www.microsoft.com" }))}>${T("Switch to external target")}</button>` : null}` : null}
    ${form.security === "tls" ? html`<div class="row2">${field("certificate", "Certificate path on node")}${field("certificate_key", "Certificate key path on node")}</div><div class="hint">${T("Place certificates under /var/lib/swg-noded/xray/tls/ on the node. Certificate renewal is managed outside this interface.")}</div>` : null}
    ${form.transport === "ws" ? field("path", "WebSocket path") : null}
    <div class="field"><label>${T("Blocked domains — one per line")}</label><textarea rows="4" value=${(form.blocked_domains || []).join("\n")} onInput=${event => change("blocked_domains", event.target.value.split(/\s+/).filter(Boolean))}/></div>
    <div class="field"><label>${T("Blocked IPs / networks — one per line")}</label><textarea rows="4" value=${(form.blocked_ips || []).join("\n")} onInput=${event => change("blocked_ips", event.target.value.split(/\s+/).filter(Boolean))}/></div>
    ${select("egress_type", "Egress", ["direct", "socks", "http"])}
    ${form.egress_type !== "direct" ? html`<div class="row2">${field("egress_host", "Proxy host")}${field("egress_port", "Proxy port", "number")}</div><div class="row2">${field("egress_user", "Proxy username")}${field("egress_password", "Proxy password", "password")}</div>` : null}
    <div class="hint">${T("Xray is a proxy inbound, not a WireGuard TUN: subnet, mesh routing and LAN sharing do not apply. Client changes briefly restart this Xray instance only.")}</div>
  <//>`;
}

export function XrayNodeCards({ node }) {
  const record = nodeRecord(node);
  return Object.entries(record.xray_cfg || {}).map(([iface, config]) => {
    const report = ((Store.stats[node] || {}).xray || []).find(instance => instance.iface === iface) || {};
    return html`<a class=${"ifcard" + (report.running ? "" : " down")} key=${iface} href=${"#/node/" + encodeURIComponent(node) + "/" + iface}>
      <div class="ifcard-top"><span class="iftype" style="color:#A78BFA">XRAY</span><span class="ifname">${iface}</span><span class="grow"></span><span>${report.running ? T("Running") : config.stopped ? T("Stopped") : T("Pending")}</span></div>
      <div class="ifrow"><span class="l">${T("Protocol")}</span><span class="r">${config.protocol.toUpperCase()} · ${config.security.toUpperCase()}</span></div>
      <div class="ifrow"><span class="l">${T("Listen")}</span><span class="r addr">${config.endpoint_host}:${config.port}</span></div>
      <div class="ifrow"><span class="l">${T("Peers")}</span><span class="r">${(report.peers || []).length}</span></div>
      ${report.error ? html`<div class="notice danger">${report.error}</div>` : null}
    </a>`;
  });
}

export function XrayConfig({ peer, target, bare }) {
  const [uri, setUri] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api.get("/api/xray/config?peer_id=" + encodeURIComponent(peer.id) + "&node=" + encodeURIComponent(target.node) + "&iface=" + encodeURIComponent(target.iface))
      .then(response => { if (active) { setUri((response.data || {}).uri || ""); setError(response.ok ? "" : srvText(response)); } })
      .catch(failure => { if (active) setError(failure.message); });
    return () => { active = false; };
  }, [peer.id, peer.xray_uuid, target.node, target.iface, Store.configEpoch]);
  const body = html`<div style="text-align:center"><b>Xray · ${target.iface}</b>${error ? html`<div class="notice danger">${error}</div>` : null}
    ${uri ? html`<${QR} conf=${uri} label=${peer.title || target.iface} px=${220}/><textarea readonly rows="4" style="width:100%" value=${uri}/><button class="btn btn-mini" onClick=${() => copy(uri)}>${T("Copy link")}</button>` : T("Loading…")}</div>`;
  return bare ? body : html`<${Sheet} title=${"Xray · " + target.iface} width=${520}>${body}<//>`;
}

function XrayPeerSheet({ node, iface }) {
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true);
    try {
      const response = await api.post("/api/peers/create", { title, targets: [{ node, iface, type: "xray" }] });
      if (!response.ok) return setError(srvText(response));
      await Store.poll();
      openModal(html`<${XrayConfig} peer=${response.data} target=${{ node, iface }}/>`);
    } catch (failure) { setError(failure.message); } finally { setBusy(false); }
  };
  return html`<${Sheet} title=${T("Create Xray peer")} err=${error} width=${520}
    foot=${footRow({ onCancel: closeModal, onAction: save, disabled: busy, action: T("Create") })}>
    <div class="field"><label>${T("Peer title")}</label><input value=${title} onInput=${event => setTitle(event.target.value)}/></div>
  <//>`;
}

export function XrayDetail({ node, iface }) {
  useStore();
  const record = nodeRecord(node), config = (record.xray_cfg || {})[iface] || {};
  const report = ((Store.stats[node] || {}).xray || []).find(instance => instance.iface === iface) || {};
  const peers = Object.values((Store.roster || {}).peers || {}).filter(peer => (peer.targets || []).some(target => target.node === node && target.iface === iface));
  const [busy, setBusy] = useState(false);
  const send = async (path, body) => {
    setBusy(true);
    try { const response = await api.post(path, body); if (!response.ok) toast(srvText(response), "err"); else await Store.poll(); }
    catch (failure) { toast(failure.message, "err"); } finally { setBusy(false); }
  };
  const action = action => send("/api/xray/action", { node, iface, action });
  const createPeer = () => openModal(html`<${XrayPeerSheet} node=${node} iface=${iface}/>`);
  return html`<div class="screen"><div class="crumb"><a href="#/nodes">${T("Nodes")}</a><span class="sep">/</span><a href=${"#/node/" + encodeURIComponent(node)}>${record.name || node}</a><span class="sep">/</span><b>${iface}</b></div>
    <${Panel} icon="globe" title=${"Xray · " + iface} actions=${html`<button class="btn btn-mini" disabled=${busy} onClick=${() => openXray(node, iface)}>${T("Edit interface")}</button>`}>
      <div class="iface-grid">${[["Protocol", config.protocol], ["Security", config.security], ["Transport", config.transport], ["Listen", config.endpoint_host + ":" + config.port], ["Status", report.running ? T("Running") : config.stopped ? T("Stopped") : T("Pending")], ["Downloaded", fmtBytes(report.tx_bytes || 0)], ["Uploaded", fmtBytes(report.rx_bytes || 0)]].map(([label, value]) => html`<div class="ig-item"><span class="ig-l">${T(label)}</span><span class="ig-v">${value}</span></div>`)}</div>
      ${report.error ? html`<div class="notice danger">${report.error}</div>` : null}
      <div class="chiprow" style="flex-wrap:wrap">${["start", "stop", "restart"].map(value => html`<button class="btn btn-mini" disabled=${busy} onClick=${() => action(value)}>${T(value[0].toUpperCase() + value.slice(1))}</button>`)}
        ${record.xray_selfhost_capable && config.security === "reality" ? html`<button class="btn btn-mini" disabled=${busy} onClick=${() => openSelfHostedXray(node, iface)}>${T(config.selfhost ? "Manage self-hosted decoy" : "Set up steal oneself")}</button>` : null}
        <button class="btn btn-mini danger" disabled=${busy} onClick=${() => openConfirm({ title: T("Delete Xray interface"), body: T("Stop this Xray server and remove its deployments. Other interfaces and peers are preserved."), danger: true, confirmLabel: T("Delete"), onConfirm: async () => { await send("/api/xray/delete", { node, iface }); location.hash = "#/node/" + encodeURIComponent(node); } })}>${T("Delete")}</button></div>
    <//>
    <${Panel} icon="users" title=${T("Peers")} count=${peers.length} actions=${html`<button class="btn btn-mini" disabled=${busy} onClick=${createPeer}><${Ic} i="plus"/>${T("Create peer")}</button>`}>
      <div class="hint">${T("Assign peers to users, edit expiry, and add more deployments using the existing roster screens. Online means recent traffic, not an idle connection.")}</div>
      ${peers.map(peer => { const traffic = (report.peers || []).find(item => item.peer_id === peer.id) || {}; return html`<div class="ifrow" key=${peer.id} style="gap:8px;flex-wrap:wrap"><span class="l">${peer.title || peer.id}${traffic.online ? " · " + T("Online") : ""}</span><span>${fmtBytes(traffic.rx_bytes || 0)} ↑ / ${fmtBytes(traffic.tx_bytes || 0)} ↓</span>
        <button class="btn btn-mini" onClick=${() => openModal(html`<${XrayConfig} peer=${peer} target=${{ node, iface }}/>`)}>QR / ${T("Link")}</button>
        <button class="btn btn-mini" disabled=${busy} onClick=${() => send(peer.disabled ? "/api/peers/unblock" : "/api/peers/block", { peer_id: peer.id })}>${T(peer.disabled ? "Unblock" : "Block")}</button>
        <button class="btn btn-mini" disabled=${busy} onClick=${() => openConfirm({ title: T("Rotate credentials"), body: T("The old Xray links stop working on every deployment of this peer."), warn: true, confirmLabel: T("Rotate"), onConfirm: () => send("/api/xray-peer/rotate", { peer_id: peer.id }) })}>${T("Rotate")}</button>
        <button class="btn btn-mini" disabled=${busy || (peer.targets || []).length < 2} onClick=${() => send("/api/peers/remove-target", { peer_id: peer.id, node, iface })}>${T("Remove deployment")}</button>
        ${!peer.user_id ? html`<button class="btn btn-mini danger" disabled=${busy} onClick=${() => openConfirm({ title: T("Delete peer"), body: T("This is irreversible — this peer is removed from every interface it is deployed on."), danger: true, confirmLabel: T("Delete"), onConfirm: () => send("/api/peers/delete", { peer_id: peer.id }) })}>${T("Delete peer")}</button>` : null}</div>`; })}
    <//></div>`;
}
