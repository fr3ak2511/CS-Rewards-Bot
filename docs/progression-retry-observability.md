# PP retry outcome tracking and escalation

Pending Progression inspections retain their existing four-slot-per-reset eligibility
and serialized recovery rules. Each attempted inspection records a sanitized outcome
category and stable fingerprint. Three consecutive attempts with the same inspection
outcome set `manual_attention_required`; a dispatched claim without same-card
Delivered confirmation is escalated immediately and retains its exact target.

Manual attention is an alert, not an automatic queue clear or retry suppression.
The queue remains pending and may be attempted in a later reset under the existing
source, slot, roster, and concurrency gates. A queue item that uses all four slots
in a reset reports its next scheduled opportunity as the next reset's Primary Run
(approximately 05:35 IST).

A login failure that occurs after a Progression slot was reserved is recorded as
the latest PP outcome. It replaces an older saved empty-scan reason, while remaining
deferred because no Progression inspection or claim click occurred.
