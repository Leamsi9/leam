import { sessionValue } from "./session-cache";

/** Old links/bookmarks retain item identity and the user's selected Today date. */
export function redirectLegacyInbox(view: string): string {
  if (view !== "inbox") return view;
  const url = new URL(location.href);
  const selected = url.hash.match(/^#today\/[^/]+\/(\d{4}-\d{2}-\d{2})$/)?.[1];
  const saved = sessionValue<{ date?: string }>("today:route", {});
  const date = selected || saved.date;
  url.searchParams.set("view", "today");
  url.hash = date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? `#today/inbox/${date}` : "#today/inbox";
  history.replaceState(history.state, "", url);
  return "today";
}
