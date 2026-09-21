import type { Data } from "./api";

const terminal = (turn: Data) =>
  ["completed", "failed", "interrupted"].includes(turn.status);

// A snapshot has no event watermark. Only a retained turn/started establishes
// an empty baseline from which deltas can be appended without guessing overlap.
export class CodingStreamProjection {
  private live = new Map<string, Data>();
  private settled = new Set<string>();
  private ended = new Set<string>();
  private completedItems = new Set<string>();

  receive(method: string, params: Data): "applied" | "unknown" | "ignored" {
    const id = params.turn?.id || params.turnId;
    if (!id || this.settled.has(id)) return "ignored";
    if (method === "turn/completed") {
      this.ended.add(id);
      return "applied"; // Final status/text are published with reconciled metadata.
    }
    if (this.ended.has(id)) return "ignored";
    if (method === "turn/started") {
      if (this.live.has(id)) return "ignored";
      this.live.set(id, {
        id,
        status: "inProgress",
        items: [...(params.turn?.items || [])],
      });
      return "applied";
    }
    const turn = this.live.get(id);
    if (!turn) return "unknown";
    const itemId = params.item?.id || params.itemId;
    if (!itemId) return "ignored";
    const key = id + ":" + itemId;
    if (this.completedItems.has(key) && method !== "item/completed")
      return "ignored";
    const index = turn.items.findIndex((item: Data) => item.id === itemId);
    const item =
      method === "item/agentMessage/delta"
        ? {
            ...(index >= 0 ? turn.items[index] : {}),
            id: itemId,
            type: "agentMessage",
            text:
              (index >= 0 ? turn.items[index].text || "" : "") +
              (params.delta || ""),
          }
        : params.item;
    if (!item) return "ignored";
    if (index < 0) turn.items.push(item);
    else turn.items[index] = item;
    if (method === "item/completed") this.completedItems.add(key);
    return "applied";
  }

  reconcile(incoming: Data[], previous: Data[] = incoming): Data[] {
    const next = incoming.map((turn) => {
      if (terminal(turn)) {
        this.settled.add(turn.id);
        this.live.delete(turn.id);
        return turn;
      }
      const known = previous.find((value) => value.id === turn.id);
      return known && terminal(known) && this.settled.has(turn.id)
        ? known
        : turn;
    });
    for (const [id, projection] of this.live) {
      const index = next.findIndex((turn) => turn.id === id);
      const base =
        index >= 0 ? next[index] : previous.find((turn) => turn.id === id);
      // Keep the user question and tool facts from history; every agent character
      // comes from this turn's replay, never from an unwatermarked snapshot.
      const retained = (base?.items || []).filter(
        (item: Data) =>
          item.type !== "agentMessage" &&
          !projection.items.some((live: Data) => live.id === item.id),
      );
      const turn = {
        ...base,
        ...projection,
        items: [...retained, ...projection.items],
      };
      if (index >= 0) next[index] = turn;
      else next.push(turn);
    }
    return next;
  }
}
