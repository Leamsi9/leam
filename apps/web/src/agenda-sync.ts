import { api, type Data } from "./api";

/** Explicit provider reads only, bounded to two concurrent requests. */
export async function syncAgendaCalendars(window: Data, signal: AbortSignal) {
  const failures: string[] = [];
  let completed = 0;
  const listing = await api("/calendar", "GET", undefined, signal);
  const accounts = (listing.accounts || []).filter(
    (item: Data) => item.state === "connected",
  );
  async function batch(items: Data[], action: (item: Data) => Promise<void>) {
    let next = 0;
    await Promise.all(
      [0, 1].map(async () => {
        while (next < items.length && !signal.aborted) {
          const item = items[next++];
          try {
            await action(item);
          } catch (error) {
            if (signal.aborted) throw error;
            failures.push(
              error instanceof Error ? error.message : String(error),
            );
          }
        }
      }),
    );
  }
  if (!accounts.length)
    return "Connect or reconnect a calendar account in Settings before syncing.";
  await batch(accounts.slice(0, 10), async (account) => {
    await api(
      `/calendar/accounts/${encodeURIComponent(account.id)}/sync`,
      "POST",
      {},
      signal,
    );
  });
  if (signal.aborted) throw new DOMException("Stopped", "AbortError");
  const refreshed = await api("/calendar", "GET", undefined, signal);
  const eligible = (refreshed.items || []).filter((item: Data) =>
    accounts
      .slice(0, 10)
      .some((account: Data) => account.id === item.accountId),
  );
  await batch(eligible.slice(0, 40), async (calendar) => {
    const result = await api(
      `/calendar/${encodeURIComponent(calendar.id)}/sync`,
      "POST",
      { start: window.start, end: window.end },
      signal,
    );
    if (result.error) throw new Error(result.error);
    completed++;
  });
  return `${completed} calendar${completed === 1 ? "" : "s"} synchronized for this day.${failures.length ? ` ${failures.length} sync request${failures.length === 1 ? "" : "s"} failed; previous saved data is retained. Check calendar sources below.` : ""}${accounts.length > 10 || eligible.length > 40 ? " Additional calendars remain; use Calendars & sync to review them." : ""}`;
}

/** Gmail metadata reads only. Consent is checked again before dispatch. */
export async function syncAgendaMail(signal: AbortSignal) {
  const overview = await api("/email", "GET", undefined, signal);
  const eligible = (overview.accounts || []).filter(
    (account: Data) =>
      account.granted === true &&
      ["never_synced", "ready", "stale", "error"].includes(account.state),
  );
  if (!eligible.length)
    return "Enable or reconnect read-only Gmail access in Email settings before syncing.";
  let completed = 0,
    failed = 0;
  // Each account has its own bounded provider operation. Do not fan out Gmail
  // metadata requests across accounts or retry a failed operation automatically.
  for (const account of eligible.slice(0, 5)) {
    if (signal.aborted) throw new DOMException("Stopped", "AbortError");
    try {
      const result = await api(
        `/email/accounts/${encodeURIComponent(account.accountId)}/sync`,
        "POST",
        {},
        signal,
      );
      if (result.state !== "ready" || result.error) failed++;
      else completed++;
    } catch (error) {
      if (signal.aborted) throw error;
      failed++;
    }
  }
  return `${completed} Gmail account${completed === 1 ? "" : "s"} synchronized.${failed ? ` ${failed} account${failed === 1 ? "" : "s"} could not sync; previous saved messages are retained. Check Email sources for details.` : ""}${eligible.length > 5 ? " Additional accounts remain; sync them in Email settings." : ""}`;
}
