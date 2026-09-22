export type SavedReceipt = { thread: string; id: string; savedAt: number; notice: string; confirmed?: boolean };
const key = (thread: string) => `leam-companion-receipt-history:${thread}`;

function readHistory(thread: string): SavedReceipt[] {
  const rows = JSON.parse(sessionStorage.getItem(key(thread)) || "[]");
  if (!Array.isArray(rows) || rows.length > 8 || rows.some(row => row.thread !== thread || typeof row.id !== "string" || row.id.length > 256))
    throw new Error("Saved receipt history cannot be read safely. Its identities were kept; check this conversation before continuing.");
  return rows;
}
export function receiptHistory(thread: string): SavedReceipt[] {
  try { return readHistory(thread); } catch { return []; }
}

/** Persist before unlocking. Never evict an unresolved identity to make room. */
export function keepReceiptIdentity(thread: string, id: string) {
  const rows = readHistory(thread);
  if (!rows.some(row => row.id === id)) {
    if (rows.length >= 8) throw new Error("Receipt history is full. Check and remove confirmed receipts in chat options before keeping another uncertain draft.");
    rows.push({ thread, id, savedAt: Date.now(), notice: "Delivery unconfirmed" });
  }
  sessionStorage.setItem(key(thread), JSON.stringify(rows));
  return rows;
}

export function updateReceiptHistory(thread: string, id: string, update: Partial<SavedReceipt> | null) {
  const rows = readHistory(thread).flatMap(row => row.id !== id ? [row] : update ? [{ ...row, ...update, id, thread }] : []);
  sessionStorage.setItem(key(thread), JSON.stringify(rows));
  return rows;
}
