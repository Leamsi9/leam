/** Confirmed domain receipts, or an explicit request to reread uncertain state. */
export type CanonicalChange =
  | {
      kind: "commitment" | "capacity";
      record: { id: string; revision: number; [key: string]: any };
    }
  | { kind: "domain"; source: "confirmed" | "inspect" };
