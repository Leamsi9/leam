import type { HTMLAttributes, ReactNode } from "react";
import { ResourceTarget, LinkedResources } from "./resource-links";
import { TicketChat } from "./ticket-chat";
import type { Data } from "./api";
import "./feature-card.css";

/** Same feature identity and compact surface before and after publication. */
export function FeatureCard({
  feature,
  children,
  className = "",
  ...props
}: HTMLAttributes<HTMLElement> & {
  feature: string;
  children: ReactNode;
  "data-backlog-feature"?: string;
  "data-unread"?: string;
}) {
  return (
    <ResourceTarget type="feature" id={feature}>
      <article {...props} className={`card feature-card ${className}`}>
        {children}
      </article>
    </ResourceTarget>
  );
}

export function FeatureCardContext({ item }: { item: Data }) {
  return (
    <div className="feature-card-context">
      <details>
        <summary>Context and links</summary>
        {item.rationale ? (
          <p>
            <strong>Why: </strong>
            {item.rationale}
          </p>
        ) : (
          <p>No rationale recorded yet.</p>
        )}
        {item.scope && (
          <p>
            <strong>Scope: </strong>
            {item.scope}
          </p>
        )}
        <LinkedResources targetType="feature" targetId={item.feature} />
      </details>
      <TicketChat ticket={item} />
    </div>
  );
}
