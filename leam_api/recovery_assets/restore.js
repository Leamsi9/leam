"use strict";
(() => {
  const get = id => document.getElementById(id);
  const terminal = new Set(["ready_for_uat", "rolled_back", "needs_review"]);
  const labels = {ready_for_uat: "Restored · ready for your testing", rolled_back: "Rollback completed", needs_review: "Needs review · no automatic retry"};
  const pendingKey = "leam-recovery-pending-operation-v1";
  let generation = 0, running = false, review = null, pending = null, timer = null;
  const validId = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || "");
  function message(text = "") { get("restore-notice").textContent = text; }
  function controls() {
    get("restore-backup").disabled = running || !!pending;
    get("restore-preview").disabled = running || !!pending || !get("restore-backup").value;
    get("restore-refresh").disabled = running;
    get("restore-execute").disabled = running || !!pending || !review || !get("restore-confirmed").checked;
    get("restore-dismiss").disabled = running;
    get("restore-history").querySelectorAll("button").forEach(button => { button.disabled = running || !!pending; });
  }
  function clearReview() { review = null; get("restore-review").hidden = true; get("restore-confirmed").checked = false; controls(); }
  function active(token) { return token === generation && authenticated; }
  function showReceipt(row, parent) {
    parent.replaceChildren();
    const title = document.createElement("p"); title.textContent = labels[row.state] || `In progress · ${row.state}`;
    const text = document.createElement("p"); text.textContent = row.message || "";
    const identity = document.createElement("p"); identity.className = "unit";
    identity.textContent = `${row.operation} · ${row.requestId} · ${new Date(row.updatedAt * 1000).toLocaleString()}`;
    parent.append(title, text, identity);
  }
  async function refreshList() {
    const token = generation;
    const data = await api("/api/restores");
    if (!active(token)) return;
    get("restore-available").hidden = !data.available;
    if (!data.available) message("Mobile restore needs operator activation of the protected data pointer. Service recovery remains available above.");
    const selected = get("restore-backup").value;
    get("restore-backup").replaceChildren();
    for (const backup of data.backups) {
      const option = document.createElement("option"); option.value = backup.id;
      option.textContent = `${new Date(backup.createdAt * 1000).toLocaleString()} · ${(backup.bytes / 1024).toFixed(0)} KB · ${backup.id.slice(0, 8)}`;
      get("restore-backup").append(option);
    }
    if ([...get("restore-backup").options].some(option => option.value === selected)) get("restore-backup").value = selected;
    if (data.available && !data.backups.length) message("No local backups in this generation. Create a backup from Leam Settings before restoring.");
    get("restore-history").replaceChildren();
    for (const row of data.history) {
      const card = document.createElement("article"); card.className = "restore-row"; showReceipt(row, card);
      if (row.operation === "restore" && row.state !== "rolled_back" && terminal.has(row.state)) {
        const button = document.createElement("button"); button.className = "secondary"; button.textContent = "Review rollback";
        button.onclick = () => preview("rollback", row.requestId);
        card.append(button);
      }
      get("restore-history").append(card);
    }
    if (review?.kind === "restore" && get("restore-backup").value !== review.id) clearReview();
    controls();
  }
  async function poll() {
    clearTimeout(timer);
    if (!authenticated || !pending) return;
    const token = generation, id = pending;
    try {
      const row = await api(`/api/restores/${encodeURIComponent(id)}`);
      if (!active(token) || pending !== id) return;
      showReceipt(row, get("restore-receipt"));
      if (terminal.has(row.state)) {
        pending = null;
        try { sessionStorage.removeItem(pendingKey); } catch (_) { /* server receipt remains authoritative */ }
        controls(); await refreshList(); return;
      }
    } catch (error) {
      if (!active(token) || pending !== id) return;
      message(`${error.message} The operation was not resent. Checking its receipt only; keep this recovery page available.`);
    }
    if (active(token) && pending) timer = setTimeout(poll, 2500);
  }
  async function preview(kind, id) {
    if (running || pending) return;
    const token = generation; running = true; clearReview(); message("Validating the selected operation…"); controls();
    try {
      const data = await api(kind === "restore" ? "/api/restores/preview" : `/api/restores/${encodeURIComponent(id)}/rollback-preview`, "POST", kind === "restore" ? {backupId: id} : {});
      if (!active(token)) return;
      if (kind === "restore" && get("restore-backup").value !== id) { message("Backup selection changed; review the selected backup again."); return; }
      review = {kind, id, data};
      get("restore-review-title").textContent = kind === "restore" ? "Review restore" : "Review rollback";
      get("restore-warning").textContent = data.warning;
      get("restore-identity").textContent = `Generation ${data.generationId || data.targetGenerationId || "verified"}${data.sourceCommit ? ` · release ${data.sourceCommit.slice(0, 12)}` : ""}${data.archiveSha256 ? ` · archive SHA-256 ${data.archiveSha256}` : ""}`;
      get("restore-execute").textContent = kind === "restore" ? "Confirm restore" : "Confirm rollback";
      get("restore-review").hidden = false; message(); get("restore-confirmed").focus();
    } catch (error) { if (active(token)) message(error.message); }
    finally { if (active(token)) { running = false; controls(); } }
  }
  async function execute() {
    if (running || pending || !review || !get("restore-confirmed").checked) return;
    const token = generation, selected = review; running = true; controls();
    let id;
    try {
      id = crypto.randomUUID();
      sessionStorage.setItem(pendingKey, id); // No side effect unless the receipt ID survives reload.
      pending = id; clearReview(); message("Request sent once. Checking its durable receipt…");
      const body = {requestId: id, previewToken: selected.data.previewToken, confirmed: true};
      if (selected.kind === "restore") body.backupId = selected.id;
      const row = await api(selected.kind === "restore" ? "/api/restores" : `/api/restores/${encodeURIComponent(selected.id)}/rollback`, "POST", body);
      if (!active(token)) return;
      showReceipt(row, get("restore-receipt"));
    } catch (error) {
      if (active(token)) {
        message(`${error.message} No automatic retry. Inspect the receipt and service status before another operation.`);
        // A definitive rejection plus an absent receipt permits a fresh review,
        // never a replay. Network uncertainty keeps the original ID pending.
        if (pending && [400,409,422].includes(error.status)) {
          try { await api(`/api/restores/${encodeURIComponent(pending)}`); }
          catch (inspection) {
            if (active(token) && inspection.status === 404) {
              pending = null;
              try { sessionStorage.removeItem(pendingKey); } catch (_) { /* next load still only inspects */ }
            }
          }
        }
      }
    } finally {
      if (active(token)) { running = false; controls(); if (pending) void poll(); }
    }
  }
  get("restore-preview").onclick = () => preview("restore", get("restore-backup").value);
  get("restore-execute").onclick = execute;
  get("restore-dismiss").onclick = clearReview;
  get("restore-confirmed").onchange = controls;
  get("restore-backup").onchange = clearReview;
  get("restore-refresh").onclick = () => { if (!running) refreshList().catch(error => message(error.message)); };
  get("restore-panel").ontoggle = () => { if (get("restore-panel").open && authenticated && !running) refreshList().catch(error => message(error.message)); };
  function onAuth(event) {
    generation++; running = false; clearTimeout(timer); pending = null; clearReview();
    get("restore-receipt").replaceChildren(); get("restore-history").replaceChildren(); get("restore-backup").replaceChildren(); message();
    if (!event.detail) return;
    try { const saved = sessionStorage.getItem(pendingKey); if (validId(saved)) pending = saved; } catch (_) { message("Receipt persistence is unavailable; restore cannot be submitted from this browser."); }
    controls();
    if (pending) { get("restore-panel").open = true; void poll(); }
    else if (get("restore-panel").open) refreshList().catch(error => message(error.message));
  }
  window.addEventListener("recovery-auth", onAuth);
  if (authenticated) onAuth({detail:true});
  controls();
})();
