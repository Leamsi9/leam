"use strict";
const $ = (id) => document.getElementById(id);
let configured = true;
let busy = false;
let authGeneration = 0;
let authenticated = false;
function authChanged(value) {
  authenticated = value;
  window.dispatchEvent(new CustomEvent("recovery-auth", {detail: value}));
}
function notice(message = "", error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
}
async function api(path, method = "GET", body) {
  const generation = authGeneration;
  const response = await fetch(path, { method, credentials: "same-origin", cache: "no-store", headers: body === undefined ? {} : {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (generation !== authGeneration) throw new Error("Recovery session changed; refresh status.");
  if (!response.ok) {
    if (response.status === 401) { authGeneration++; authChanged(false); $("auth").hidden = false; $("dashboard").hidden = true; }
    const error = new Error(typeof data.detail === "string" ? data.detail : "Check the entered values and try again.");
    error.status = response.status;
    throw error;
  }
  return data;
}
function disable(value) {
  busy = value;
  document.querySelectorAll("#login button, #services button, #refresh").forEach(button => { button.disabled = value; });
}
function showServices(data) {
  $("services").replaceChildren();
  for (const service of data.services) {
    const card = document.createElement("article"); card.className = "service";
    const title = document.createElement("h3"); title.textContent = service.name;
    const state = document.createElement("p"); state.className = "state";
    state.textContent = `${service.active} · ${service.sub} · ${service.reachable ? "port reachable" : "port unavailable"}`;
    const unit = document.createElement("p"); unit.className = "unit"; unit.textContent = service.unit;
    const actions = document.createElement("div"); actions.className = "actions";
    for (const action of ["start", "restart"]) {
      const button = document.createElement("button");
      button.textContent = `${action === "start" ? "Start" : "Restart"} ${service.name}`;
      button.className = action === "restart" ? "secondary" : "";
      button.disabled = busy;
      button.onclick = async () => {
        if (busy) return;
        if (action === "restart" && !confirm(`Restart ${service.name}? Active work in this service may be interrupted.`)) return;
        disable(true); notice(`${action === "start" ? "Starting" : "Restarting"} ${service.name}…`);
        try { showServices(await api(`/api/services/${encodeURIComponent(service.id)}/${action}`, "POST", {})); notice("Service manager completed the request. Current status is shown below."); }
        catch (error) { notice(`${error.message} Inspect status before retrying.`, true); }
        finally { disable(false); }
      };
      actions.append(button);
    }
    card.append(title, state, unit, actions); $("services").append(card);
  }
  $("checked").textContent = `Checked ${new Date(data.checkedAt * 1000).toLocaleTimeString()}`;
}
async function refresh() { showServices(await api("/api/services")); }
async function status() {
  const auth = await api("/api/auth/status"); configured = auth.configured;
  $("auth").hidden = auth.authenticated; $("dashboard").hidden = !auth.authenticated;
  $("auth-title").textContent = configured ? "Sign in" : "Pair recovery access";
  $("code-label").hidden = configured; $("pairing-help").hidden = configured;
  $("code").required = !configured;
  $("sign-in").textContent = configured ? "Sign in to recovery" : "Pair recovery access";
  $("password").autocomplete = configured ? "current-password" : "new-password";
  $("password").minLength = configured ? 1 : 12;
  authChanged(auth.authenticated);
  if (auth.authenticated) await refresh();
}
$("login").onsubmit = async (event) => {
  event.preventDefault(); if (busy) return; authGeneration++; disable(true); notice();
  try {
    const body = { password: $("password").value };
    if (!configured) body.bootstrap = $("code").value;
    await api(configured ? "/api/auth/login" : "/api/auth/setup", "POST", body);
    $("password").value = ""; $("code").value = ""; await status();
  } catch (error) { notice(error.message, true); }
  finally { disable(false); }
};
$("refresh").onclick = async () => { if (busy) return; disable(true); try { await refresh(); notice(); } catch(error) { notice(error.message, true); } finally { disable(false); } };
$("logout").onclick = async () => { authGeneration++; authChanged(false); $("dashboard").hidden = true; try { await api("/api/auth/logout", "POST", {}); await status(); notice(); } catch(error) { notice(error.message, true); } };
status().catch(error => notice(error.message, true));
