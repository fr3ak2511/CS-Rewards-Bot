# Generic temporary event reward discovery

## Current verified state

The reusable event claimant is already separate from the permanent grenade-based Progression Program. It accepts multiple active entries from `event_rewards_config.json`, enforces start/end windows, reads the balance and tier ladder, checks the exact tier/button before clicking, and counts a claim only after the portal shows the tier as Delivered.

The current event integration is **configuration-driven, not auto-discovered**. The configuration must supply the programme name, slug, programme ID, points label, start/end timestamps, cycle and optional expected tier count. The current page adapter also uses the verified Halloween card selector (`div.relative.rounded-lp-card`). `event_closeout.py` is a one-time Halloween closeout and is not a generic event lifecycle manager.

The public `https://hub.vertigogames.co/events` page is a news/announcement listing. It is not evidence of an authenticated account reward-programme directory or of a programme discovery API.

## Why automatic discovery is not enabled yet

No authenticated event-catalog response, stable event-directory DOM contract, or active non-Halloween event page fixture is present in the repository. Guessing a slug from a campaign title, assuming an undocumented API, or treating every news post as a claimable rewards programme could direct the bot to an unrelated page. That is not acceptable for code that clicks reward buttons across 35 accounts.

The Halloween 2026 event expired on 5 November 2026. It cannot serve as a live end-to-end fixture for a future programme.

## Target design

1. **Discover, do not infer.** Read the portal's canonical authenticated event-programme registry or explicit event cards. A candidate must expose a stable programme ID, route/slug, name, start/end times, point label, and cycle/season identifier. Do not manufacture missing fields from a title.
2. **Normalize into a common descriptor.** Convert verified source data into the existing event-programme schema. Support multiple simultaneously active programmes and deduplicate by stable programme ID plus cycle.
3. **Use a UI adapter per verified layout.** Reuse the current tier-card adapter only when its card structure and threshold markers match. Unknown page layouts are discovery-only: log a diagnostic and do not click.
4. **Preserve claim safeguards.** Require a readable balance, unique thresholds, sufficient balance, an exact visible/enabled Claim control in its own tier card, safe popup state, and a Delivered confirmation. Any ambiguity fails closed.
5. **Keep programme history independent.** Store confirmed claims by account, programme ID, cycle and threshold. Expiry closes the active view but never deletes historical claim ledgers.
6. **Make discovery observable.** Record candidate source, parsed metadata, unsupported-layout reason, duplicate/ambiguous IDs, and discovery timestamp in the run artifact. Do not place credentials, cookies or full account identifiers in the artifact.

## Required evidence to implement the portal adapter

When a new temporary rewards event is live, capture one authenticated account's event page and the relevant Fetch/XHR response that contains the programme metadata. The capture must omit request headers, cookies, tokens, and private account details. A DOM snapshot of the programme listing plus one event detail page is also sufficient if it includes the metadata fields above.

That real fixture is needed to identify the canonical source and verify whether the current card adapter can be reused. After the adapter is implemented, the offline suite must cover: no active events; one and multiple active programmes; future and expired programmes; duplicate IDs; missing or malformed metadata; unknown page layout; duplicate thresholds; balance/threshold inconsistency; popup obstruction; click ambiguity; Delivered confirmation; expiry; and idempotent per-cycle history.

## Release rule

Do not enable automatic event discovery in production until the discovered programme metadata and its page adapter pass the offline tests and a monitored live validation on a real temporary event. Until then, the existing configured event engine remains fail-closed and does not guess new event routes.
