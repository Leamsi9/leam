import type { ReactNode } from "react";
import "./semantic-colours.css";

export type SemanticTone =
  "neutral" | "info" | "success" | "warning" | "danger" | "violet" | "personal";
const tones: Record<string, SemanticTone> = {
  todo: "neutral",
  ready: "info",
  handover: "warning",
  in_progress: "info",
  blocked: "danger",
  completed: "success",
  paused: "warning",
  pending: "warning",
  proposals_pending: "warning",
  queued: "warning",
  clarification_needed: "warning",
  executing: "info",
  checking: "info",
  deployed: "info",
  running: "info",
  complete: "success",
  passed: "success",
  changes_confirmed_complete: "success",
  failed: "danger",
  check_failed: "danger",
  conflict: "danger",
  rejected: "danger",
  owner_user: "personal",
  owner_leam: "violet",
  priority_high: "warning",
  priority_low: "neutral",
  priority_normal: "neutral",
};
/** Explicit known states only. Unknown values never acquire a success colour. */
export function semanticTone(value: string): SemanticTone {
  return Object.hasOwn(tones, value) ? tones[value] : "neutral";
}
export function SemanticBadge({
  value,
  children,
  className = "",
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`semantic-colour semantic-badge ${className}`}
      data-tone={semanticTone(value)}
    >
      {children}
    </span>
  );
}
