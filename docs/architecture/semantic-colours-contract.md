# Semantic colours

`semantic-colours.css` defines warm light surfaces, dark label text, borders and strong chart colours. `semantic-badge.tsx` maps explicit known state enums to tones; unknown values are neutral. No text inference, authority or state transition depends on a colour.

| Tone | Meaning |
| --- | --- |
| Slate | Not started, normal/low priority, unknown or inactive |
| Blue | In progress, execution awaiting confirmation, information |
| Amber | Waiting/review, clarification, high priority, paused |
| Red | Failed, blocked, conflict |
| Green | Confirmed success, passed check or completed card lifecycle |
| Violet | Leam ownership (descriptive, never execution) |
| Teal | Human ownership |

Status labels remain visible. Priority has its own label: high priority is amber, not failure red. Approval unread dots retain their existing purple identity distinct from Updates. UAT pending remains amber; deployed does not imply QA/UAT completion. Today check colour follows the recorded outcome; no-action/unknown does not gain success colouring.

Boards use the same status tone for card borders, Kanban headers, summary segments and timeline bars. Ownership and priority stay separate labelled badges. A deadline diamond is amber; its explicit date remains in text. Timeline duration still requires actual start and end dates. The collapsed summary counts loaded commitment cards in the current capacity filter by lifecycle/stage and owner, including habit/goal cards; it excludes daily progress and subtasks. Its HTML table provides exact values and a text alternative to the decorative proportional bar. It is not a productivity measure or completion-rate claim.

Rendered badge text targets at least 4.5:1 contrast against its surface, and meaningful chart graphics 3:1 against their background. Phone view preserves compact controls, wrapping labels and internal horizontal scrolling. Forced-colour mode retains badge borders; text and numeric tables remain authoritative when hue is unavailable.
