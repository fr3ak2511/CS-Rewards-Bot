# PP authenticated inspection diagnostics

The read-only Progression diagnostic envelope records bounded visible DOM hints:
current Progression route, document readiness, viewport, candidate horizontal
container geometry, visible child-card snippets and exact visible status/button text.
It does not change scroll position, click, dismiss overlays or submit a claim.

Every diagnostic is explicitly marked `completeness_verdict=not_established` and
`delivery_verdict=not_established`. A visible Claim/Delivered/Locked label is only
a text hint. It is not a milestone ledger state. Full carousel coverage must be
proven using an authenticated layout fixture and a verified per-layout adapter
before any card can be marked Delivered, Claimable, or Locked.

The JSON diagnostic summary is included in the existing `run_artifacts/run_summary.json`
artifact uploaded by the normal workflow. Text is capped and sensitive-looking
identifiers, email addresses, URLs and credential-like substrings are redacted.
