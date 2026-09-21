import { useEffect, useRef, useState } from "react";
import { Square } from "lucide-react";
import { api } from "./api";

/** Exact-turn control: never substitute whichever turn happens to be active later. */
export function SharedStop({
  turnId,
  generation,
  connected,
}: {
  turnId: string;
  generation: string;
  connected: boolean;
}) {
  const [notice, setNotice] = useState("");
  const [sent, setSent] = useState(false);
  const busy = useRef(false);
  const current = useRef("");
  const identity = `${generation}:${turnId}`;
  current.current = identity;
  useEffect(() => {
    setNotice("");
    setSent(false);
    busy.current = false;
    return () => {
      current.current = "";
    };
  }, [identity]);
  async function stop() {
    if (busy.current || sent || !connected) return;
    busy.current = true;
    setSent(true);
    setNotice("Requesting stop…");
    const target = identity;
    try {
      const receipt = await api("/codex/shared/interrupt", "POST", {
        turnId,
        generation,
      });
      if (current.current === target) setNotice(receipt.detail);
    } catch {
      if (current.current === target)
        setNotice(
          "Stop status is unknown. Check this turn in Codex before trying again.",
        );
    }
  }
  return (
    <span className="shared-stop">
      <button
        type="button"
        className="icon-button"
        aria-label="Stop current shared turn"
        title="Stop this turn and its child agents. The goal remains active."
        disabled={!connected || sent}
        onClick={() => void stop()}
      >
        <Square size={18} aria-hidden="true" />
      </button>
      {notice && <small role="status">{notice}</small>}
    </span>
  );
}
