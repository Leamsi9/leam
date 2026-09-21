import type { Data } from "./api";
import { rememberSession, sessionValue } from "./session-cache";

export type CodingEcho = {
  id: string;
  text: string;
  state: "sending" | "uncertain" | "accepted";
  turnId?: string;
};
export function codingEchoes(threadId: string): CodingEcho[] {
  const value = sessionValue<unknown>("coding:echoes:" + threadId, []);
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (v): v is CodingEcho =>
        v &&
        typeof v.id === "string" &&
        v.id.length > 0 &&
        v.id.length <= 256 &&
        typeof v.text === "string" &&
        v.text.length <= 100000 &&
        ["sending", "uncertain", "accepted"].includes(v.state),
    )
    .slice(-8)
    .map((v) => ({
      ...v,
      state: v.state === "sending" ? "uncertain" : v.state,
    }));
}
export function saveCodingEchoes(threadId: string, echoes: CodingEcho[]) {
  rememberSession("coding:echoes:" + threadId, echoes.slice(-8));
}
export function unrepresentedEchoes(
  echoes: CodingEcho[],
  turns: Data[],
): CodingEcho[] {
  const represented = new Set<string>();
  for (const turn of turns)
    for (const item of turn.items || []) {
      if (item.type !== "userMessage") continue;
      for (const id of [item.clientId, item.clientUserMessageId])
        if (typeof id === "string" && id.length > 0 && id.length <= 256)
          represented.add(id);
    }
  return echoes.filter((echo) => !represented.has(echo.id));
}
