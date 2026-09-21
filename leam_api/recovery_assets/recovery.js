"use strict";
const $ = (id) => document.getElementById(id);
let configured = true;
let busy = false;
function notice(message = "", error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
}
async function api(path, method = "GET", body) {
  const response = await fetch(path, { method, credentials: "same-origin", cache: "no-store", headers: body === undefined ? {} : {"Content-Type": "application/json"}, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { $("auth").hidden = false; $("dashboard").hidden = true; }
    throw new Error(typeof data.detail === "string" ? data.detail : "Check the entered values and try again.");
  }
  return data;
}
function disable(value) {
  busy = value;
  document.querySelectorAll("button").forEach(button => { button.disabled = value; });
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
  if (auth.authenticated) await refresh();
}
$("login").onsubmit = async (event) => {
  event.preventDefault(); if (busy) return; disable(true); notice();
  try {
    const body = { password: $("password").value };
    if (!configured) body.bootstrap = $("code").value;
    await api(configured ? "/api/auth/login" : "/api/auth/setup", "POST", body);
    $("password").value = ""; $("code").value = ""; await status();
  } catch (error) { notice(error.message, true); }
  finally { disable(false); }
};
$("refresh").onclick = async () => { if (busy) return; disable(true); try { await refresh(); notice(); } catch(error) { notice(error.message, true); } finally { disable(false); } };
$("logout").onclick = async () => { try { await api("/api/auth/logout", "POST", {}); await status(); notice(); } catch(error) { notice(error.message, true); } };
status().catch(error => notice(error.message, true));
