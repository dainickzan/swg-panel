import { T, srvText } from "./i18n.js";
import { Store, api } from "./store.js";
import { BASE, V } from "./util.js";
import { Sheet, footRow, openModal, closeModal, copy, Ic } from "./ui.js";
import { h } from "preact";
import { useState } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);
export const FORK_REPOSITORY = "dainickzan/swg-panel";
export const BOOTSTRAP_URL = "https://raw.githubusercontent.com/" + FORK_REPOSITORY + "/main/bootstrap.sh";
export const shellQuote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
export const bootCmd = args => `sudo bash -c "$(curl -fsSL ${BOOTSTRAP_URL})" -- ${args}`;

export function installationCommands(settings) {
  const { role, method, domain, endpoint, name, panelUrl, token, username, tls } = settings;
  if (!["master", "host", "node"].includes(role) || !["docker", "baremetal"].includes(method)) throw new Error("Invalid installation role or method");
  const assignments = { SWG_REPO: "https://github.com/" + FORK_REPOSITORY, SWG_REF: "main" };
  if (role === "node") {
    assignments.PANEL_URL = panelUrl;
    assignments.NODE_TOKEN = token || "<ENROLLMENT_TOKEN>";
    assignments[method === "docker" ? "NODE_ENDPOINT" : "ENDPOINT_IP"] = endpoint;
    assignments.NODE_NAME = name;
  } else {
    assignments.PANEL_DOMAIN = domain;
    if (method === "docker") {
      Object.assign(assignments, { PANEL_USER: username, TLS: tls, BUILD: "true" });
      if (role === "master") Object.assign(assignments, { NODE_ENDPOINT: endpoint, NODE_NAME: name });
    } else {
      Object.assign(assignments, { ROLE: role, BASIC_USER: username, TLS_MODE: tls, SERVE_MODE: "internal" });
      if (role === "master") Object.assign(assignments, { HOST_ENDPOINT_IP: endpoint, HOST_NODE_NAME: name });
    }
  }
  if (method === "docker") assignments.BUILD = "true";
  const environment = Object.entries(assignments).filter(([, value]) => value).map(([key, value]) => key + "=" + shellQuote(value)).join(" ");
  const args = (method === "docker" ? "docker " : "") + role + (method === "docker" ? " --build" : "");
  const command = `sudo ${environment} bash -c "$(curl -fsSL ${BOOTSTRAP_URL})" -- ${args}`;
  return { install: command, preview: command + " --dry-run" };
}

export function openInstallCommands() {
  openModal(html`<${InstallCommandsSheet}/>`);
}

export function InstallCommandsSheet() {
  const [role, setRole] = useState("node");
  const [method, setMethod] = useState("docker");
  const [domain, setDomain] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [name, setName] = useState("");
  const [username, setUsername] = useState("admin");
  const [tls, setTls] = useState("selfsigned");
  const [panelUrl, setPanelUrl] = useState(Store.panelPublicUrl || location.origin + BASE);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const clearNode = setter => value => { setter(value); setToken(""); };
  const field = (label, value, setter, disabled = false) => html`<div class="field"><label>${T(label)}</label><input value=${value} disabled=${busy || disabled} onInput=${event => setter(event.target.value)}/></div>`;
  const commands = installationCommands({ role, method, domain: domain.trim(), endpoint: endpoint.trim(), name: name.trim(), panelUrl: panelUrl.trim(), token, username: username.trim(), tls });
  const createToken = async () => {
    if (!V.nodeName(name.trim())) { setError(T("Name: 1–40 chars, letters/digits/-/_ only.")); return; }
    setBusy(true); setError("");
    try {
      const response = await api.nodeCreate({ name: name.trim(), endpoint_host: endpoint.trim() });
      if (!response.ok) setError(srvText(response));
      else { setToken(response.data.token); await Store.poll(); }
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  const block = (label, value) => html`<div class="field"><label>${T(label)}</label><div class="cmdrow"><pre class="tokenbox" style="white-space:pre-wrap;overflow-wrap:anywhere;margin:0">${value}</pre><button class="copyaction" onClick=${() => copy(value, T("Copied"))}><${Ic} i="copy"/>${T("Copy")}</button></div></div>`;
  return html`<${Sheet} title=${T("Quick server setup · Xray build")} width=${800} err=${error} foot=${footRow({ onCancel: closeModal })}>
    <div class="hint">${T("These commands install our fork with Xray and self-hosted REALITY. Run the preview first, then the installer on the target Linux server. No command runs from this panel.")}</div>
    <div class="row2"><div class="field"><label>${T("Server role")}</label><select value=${role} disabled=${busy} onChange=${event => setRole(event.target.value)}>
      <option value="node">${T("Node · connect to this panel")}</option><option value="master">${T("Master · panel + entry server")}</option><option value="host">${T("Host · panel only")}</option></select></div>
      <div class="field"><label>${T("Installation method")}</label><select value=${method} disabled=${busy} onChange=${event => setMethod(event.target.value)}><option value="docker">Docker</option><option value="baremetal">${T("Bare-metal")}</option></select></div></div>
    ${role !== "node" ? html`${field("New panel domain or IP", domain, setDomain)}${field("Admin username", username, setUsername)}<div class="field"><label>${T("Panel certificate")}</label><select value=${tls} onChange=${event => setTls(event.target.value)}><option value="selfsigned">${T("Self-signed · works with IP")}</option><option value="letsencrypt">Let's Encrypt</option></select></div><div class="hint">${T("The installer asks for the admin password and certificate email on the server. Passwords are not included in copied commands. Master auto-enrolls its local node; host creates no entry node.")}</div>` : null}
    ${role !== "host" ? html`${field("Node name", name, clearNode(setName), !!token)}${field("Public endpoint IP or hostname", endpoint, clearNode(setEndpoint), !!token)}` : null}
    ${role === "node" ? html`${field("Panel URL", panelUrl, setPanelUrl, !!token)}<button class="btn btn-mini" disabled=${busy || !!token || !name.trim() || !endpoint.trim()} onClick=${createToken}>${T(token ? "Node enrollment created" : "Create node + enrollment command")}</button><div class="hint">${T("Create a new node only for a new server. For an existing server use its enrollment/recovery dialog to preserve its identity. Enrollment commands contain a secret: share them only with the server administrator.")}</div>` : null}
    <div class="hint">${T("Docker builds our checked-out source instead of silently pulling upstream images. Bare-metal keeps WG/AWG native and installs Docker for isolated Xray containers. Initial REALITY decoy setup remains in the interface wizard.")}</div>
    ${block("Preview · no installation", commands.preview)}${block("Install on target server", commands.install)}
    <div class="hint">${T("A blank field is asked interactively. A node command needs the generated enrollment token. Provider firewall ports must be allowed separately.")}</div>
  <//>`;
}
