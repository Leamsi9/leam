"use strict";
(() => {
  let state = null, running = false, generation = 0;
  const notice = (text = "") => { $("maintenance-notice").textContent = text; };
  function render() {
    $("maintenance-prepare").disabled = running;
    $("maintenance-refresh").disabled = running;
    $("maintenance-release").disabled = running;
    $("maintenance-release").hidden = !state?.held;
    $("maintenance-prepare").textContent = state?.held ? "Check accepted work" : "Prepare maintenance";
  }
  async function refresh() {
    const token = generation;
    const value = await api("/api/maintenance");
    if (token !== generation || !authenticated) return;
    state = value.maintenance;
    notice(state.held ? (state.phase === "held" ? "Maintenance held. Restore and rollback can be reviewed. End maintenance explicitly after checking the result." : "New work is paused. Check accepted work again; no active work is cancelled.") : "Normal operation. Maintenance is not active.");
    render();
  }
  async function action(kind) {
    if (running || !authenticated) return;
    const token = generation;
    running = true; render();
    try {
      // Read the authoritative operation identity; never replace a lost-response
      // operation with an automatically replayed action.
      const current = await api("/api/maintenance");
      if (token !== generation || !authenticated) return;
      if (kind === "release" && !current.maintenance.held) { await refresh(); return; }
      if (kind === "release" && !confirm("End maintenance and allow new work? Complete operator restore/rollback verification first. Scheduled automations remain paused until their separate Settings review.")) return;
      const requestId = current.maintenance.requestId || crypto.randomUUID();
      const result = await api(`/api/maintenance/${kind}`, "POST", {requestId});
      if (token !== generation || !authenticated) return;
      state = result.maintenance;
      await refresh();
      if (kind === "prepare" && !result.idle) {
        const reason = result.native?.native?.reason || result.native?.reason || result.companion?.reason || "Let accepted work finish, then check again.";
        notice(`New work is paused. Accepted work is still active or uncertain. ${reason} Nothing was interrupted.`);
      }
    } catch (error) {
      if (token === generation && authenticated) notice(`${error.message} Refresh status before another action; no automatic retry.`);
    } finally {
      if (token === generation) { running = false; render(); }
    }
  }
  $("maintenance-prepare").onclick = () => action("prepare");
  $("maintenance-release").onclick = () => action("release");
  $("maintenance-refresh").onclick = () => refresh().catch(error => notice(error.message));
  window.addEventListener("recovery-auth", event => {
    generation++; state = null; running = false; render(); notice();
    if (event.detail) refresh().catch(error => { if (authenticated) notice(error.message); });
  });
})();
