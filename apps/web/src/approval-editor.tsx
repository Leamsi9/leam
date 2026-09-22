import { useState } from "react";
import type { Data } from "./api";
const labels: Record<string, string> = {
  title: "Title",
  name: "Name",
  kind: "Kind",
  measure: "Measure",
  target: "Target",
  notes: "Notes",
  note: "Notes",
  record: "Record",
  status: "Status",
  capacityId: "Capacity",
  startDate: "First day",
  endDate: "Last day",
  timezone: "Timezone",
  reminderTime: "Reminder",
  reward: "Reward",
  date: "Day",
  time: "Time",
  minutes: "Duration in minutes",
  fold: "Repeated clock hour",
  operation: "Progress action",
  value: "Amount",
  instructions: "Coding task",
  context: "Context",
  text: "Text",
  source: "Source",
  category: "Category",
};
const choices: Record<string, string[]> = {
  kind: ["task", "habit", "goal"],
  measure: ["boolean", "count", "minutes"],
  status: ["active", "completed", "paused"],
  operation: ["set", "toggle", "complete"],
};
const nullable = new Set([
  "capacityId",
  "startDate",
  "endDate",
  "reminderTime",
]);
const multiline = new Set([
  "notes",
  "note",
  "record",
  "instructions",
  "context",
  "text",
]);
const numeric = new Set(["target", "value", "minutes", "fold"]);
export function ApprovalEditor({
  item,
  capacities,
  busy,
  save,
  cancel,
}: {
  item: Data;
  capacities: Data[];
  busy: boolean;
  save: (input: Data) => Promise<void>;
  cancel: () => void;
}) {
  const [input, setInput] = useState<Data>(() =>
    JSON.parse(JSON.stringify(item.input)),
  );
  function fields(value: Data, update: (next: Data) => void, prefix = "") {
    const keys = new Set(Object.keys(value));
    if (
      !prefix &&
      item.operation.startsWith("commitment.") &&
      item.operation !== "commitment.progress"
    )
      for (const key of [
        "title",
        "notes",
        "capacityId",
        "startDate",
        "endDate",
        "reminderTime",
      ])
        keys.add(key);
    return [...keys].flatMap((key) => {
      if (
        (key === "edit" || key === "schedule") &&
        value[key] &&
        typeof value[key] === "object"
      )
        return (
          <fieldset key={key}>
            <legend>Event details</legend>
            {fields(
              value[key],
              (next) => update({ ...value, [key]: next }),
              key,
            )}
          </fieldset>
        );
      if (!labels[key]) return [];
      const change = (raw: string) =>
        update({
          ...value,
          [key]: numeric.has(key)
            ? raw === ""
              ? null
              : Number(raw)
            : nullable.has(key) && !raw
              ? null
              : raw,
        });
      const label = labels[key];
      return (
        <label key={key}>
          {label}
          {key === "capacityId" ? (
            <select
              aria-label={label}
              value={value[key] || ""}
              onChange={(event) => change(event.target.value)}
              disabled={busy}
            >
              <option value="">Personal</option>
              {value[key] &&
                !capacities.some((capacity) => capacity.id === value[key]) && (
                  <option value={value[key]}>Unavailable capacity</option>
                )}
              {capacities.map((capacity) => (
                <option key={capacity.id} value={capacity.id}>
                  {capacity.name}
                </option>
              ))}
            </select>
          ) : choices[key] ? (
            <select
              aria-label={label}
              value={value[key] || ""}
              onChange={(event) => change(event.target.value)}
              disabled={busy}
            >
              {!value[key] && <option value="">Unchanged</option>}
              {choices[key].map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : multiline.has(key) ? (
            <textarea
              rows={3}
              value={value[key] ?? ""}
              onChange={(event) => change(event.target.value)}
              disabled={busy}
            />
          ) : (
            <input
              type={
                numeric.has(key)
                  ? "number"
                  : ["date", "startDate", "endDate"].includes(key)
                    ? "date"
                    : ["time", "reminderTime"].includes(key)
                      ? "time"
                      : "text"
              }
              step={numeric.has(key) ? "any" : undefined}
              value={value[key] ?? ""}
              onChange={(event) => change(event.target.value)}
              disabled={busy}
            />
          )}
        </label>
      );
    });
  }
  return (
    <form
      className="settings-form approval-editor"
      aria-label="Edit approval"
      onSubmit={(event) => {
        event.preventDefault();
        void save(input);
      }}
    >
      <p>
        Save a revised proposal, then review and approve it. Saving does not
        apply the change.
      </p>
      {fields(input, setInput)}
      <div className="actions">
        <button disabled={busy}>Save revised proposal</button>
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={cancel}
        >
          Cancel edit
        </button>
      </div>
    </form>
  );
}
