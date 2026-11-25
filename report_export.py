"""Self-contained, mobile-first run report with a rich-copy action.

The email remains email-client-safe and script-free. This companion HTML artifact
is intended to be opened in a browser, where its Copy Report button tries a PNG image,
then formatted HTML and plain text, with a plain-text fallback.
"""

from __future__ import annotations

import html
from typing import Any

from cycle_health_report import render_cycle_health_overview


def _number(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe(value: Any, fallback: str = "—") -> str:
    text = str(value if value is not None else "").strip()
    return html.escape(text or fallback)


def _player_label(player: dict[str, Any]) -> str:
    name = str(player.get("display_name") or "").strip()
    if name:
        return name[:80]
    suffix = str(player.get("player_id_last4") or "").strip()
    return f"Player …{suffix}" if suffix else "Unknown player"


def _deferred_reason_summary(raw_reason: Any) -> str:
    """Summarise a deferred inspection reason without copying raw popup DOM context."""
    raw = str(raw_reason or "").strip()
    if not raw:
        return "Popup/overlay safety guard blocked Progression inspection"
    summary = raw.split("popup_context=", 1)[0].split(";", 1)[0].strip().rstrip(" ,")
    if summary.endswith("("):
        summary = summary[:-1].rstrip()
    if "(" in summary and not summary.endswith(")"):
        summary += ")"
    return summary[:180].rstrip()


def _deferred_progression_is_only_issue(player: dict[str, Any]) -> bool:
    """Identify an inspection-only deferral without hiding unrelated claim failures."""
    if not player.get("progression_deferred") or player.get("progression_unconfirmed"):
        return False
    if str(player.get("status") or "") in {"Login Failed", "Error", "Failed", "No Rewards"}:
        return False
    if (
        player.get("daily_unconfirmed")
        or player.get("standard_store_unconfirmed_indices")
        or player.get("promotion_detection_failed")
    ):
        return False
    if any(
        isinstance(event, dict) and event.get("retry_required")
        for event in (player.get("event_rewards") or [])
    ):
        return False
    balance = player.get("grenade_balance")
    if isinstance(balance, dict) and str(balance.get("status") or "") in {
        "below_minimum", "counter_regressed"
    }:
        return False
    carry = player.get("grenade_balance_carry_forward")
    if isinstance(carry, dict) and str(carry.get("status_at_observation") or "") in {
        "below_minimum", "counter_regressed"
    }:
        return False
    fail_reason = str(player.get("fail_reason") or "").strip()
    deferred_reason = str(player.get("progression_deferred_reason") or "").strip()
    reason_lower = (fail_reason or deferred_reason).lower()
    if any(marker in reason_lower for marker in (" raised ", "exception", "runtime error", "could not authenticate")):
        return False
    known_nonclaim_inspection = any(marker in reason_lower for marker in (
        "no currently visible claimable reward",
        "popup/overlay safety guard",
        "unknown_fullscreen_overlay_blocks_page",
        "does not inspect progression",
        "held for this backup slot",
    ))
    return known_nonclaim_inspection and (not fail_reason or fail_reason == deferred_reason)


def _progression_milestone_table(players: list[dict[str, Any]]) -> str:
    """Group accounts by the portal's next milestone, Grenade balance and PP outcome."""
    grouped: dict[tuple[Any, ...], list[str]] = {}
    for player in players:
        if not isinstance(player, dict):
            continue
        if str(player.get("processing_mode") or "full_claim") == "promotional_only":
            continue
        balance = player.get("grenade_balance")
        carry = player.get("grenade_balance_carry_forward")
        balance = balance if isinstance(balance, dict) else {}
        carry = carry if isinstance(carry, dict) else {}
        claims = max(_number(player.get("progression_claimed_this_run")), _number(player.get("progression")))
        deferred = bool(player.get("progression_deferred"))
        unconfirmed = bool(player.get("progression_unconfirmed"))
        checked = player.get("progression_checked") is True

        if balance.get("current_count") is not None:
            actual = _number(balance.get("current_count"))
            minimum = _number(balance.get("expected_minimum")) if balance.get("expected_minimum") is not None else None
            milestone = _number(balance.get("next_milestone")) if balance.get("next_milestone") is not None else None
            remaining = _number(balance.get("remaining_to_next_milestone")) if balance.get("remaining_to_next_milestone") is not None else (
                max(0, milestone - actual) if milestone is not None else None
            )
            balance_status = str(balance.get("status") or "not_validated")
            prior_status = str(carry.get("status_at_observation") or "")
            prior_date = str(carry.get("last_observed_date") or "")
            if prior_status in {"below_minimum", "counter_regressed", "status_not_retained"}:
                prior_label = {
                    "below_minimum": "previous reading below date minimum",
                    "counter_regressed": "previous counter regression",
                    "status_not_retained": "previous status not retained",
                }[prior_status]
                freshness = "Current run; " + prior_label + (f" on {prior_date}" if prior_date else "")
            else:
                freshness = "Current run"
        elif carry.get("last_observed_count") is not None:
            actual = _number(carry.get("last_observed_count"))
            minimum = _number(carry.get("expected_minimum_at_observation")) if carry.get("expected_minimum_at_observation") is not None else (
                _number(carry.get("expected_minimum_as_of")) if carry.get("expected_minimum_as_of") is not None else None
            )
            milestone = _number(carry.get("next_milestone_at_observation")) if carry.get("next_milestone_at_observation") is not None else None
            remaining = _number(carry.get("remaining_to_milestone_at_observation")) if carry.get("remaining_to_milestone_at_observation") is not None else (
                max(0, milestone - actual) if milestone is not None else None
            )
            balance_status = str(carry.get("status_at_observation") or "not_validated")
            freshness = "Last observed " + str(carry.get("last_observed_date") or "date unavailable")
            current_minimum = carry.get("expected_minimum_as_of")
            if current_minimum is not None and minimum is not None and int(current_minimum) > int(minimum):
                freshness += f" · current date minimum {current_minimum}"
        else:
            actual = minimum = milestone = remaining = None
            balance_status = "not_validated"
            freshness = "Not captured"

        if unconfirmed:
            pp_status = "Claim confirmation unresolved"
        elif claims:
            reward_word = "reward" if claims == 1 else "rewards"
            pp_status = f"{claims} {reward_word} confirmed" + (" · inspection still pending" if deferred else "")
        elif deferred:
            reason = str(player.get("progression_deferred_reason") or "").lower()
            if "no currently visible claimable reward" in reason:
                pp_status = "Pending · no claimable card visible"
            elif "popup/overlay" in reason or "fullscreen_overlay" in reason:
                pp_status = "Pending · blocked by overlay"
            else:
                pp_status = "Pending inspection"
        elif checked:
            pp_status = "Checked · no reward claimed"
        elif str(player.get("status") or "") == "Login Failed":
            pp_status = "Not checked · login failed"
        else:
            pp_status = "Not checked in this run"

        key = (
            milestone if milestone is not None else 10**9,
            actual if actual is not None else 10**9,
            minimum if minimum is not None else 10**9,
            remaining if remaining is not None else 10**9,
            pp_status,
            balance_status,
            freshness,
        )
        suffix = str(player.get("player_id_last4") or "").strip()
        label = _player_label(player)
        if suffix:
            label += f" (…{suffix})"
        grouped.setdefault(key, []).append(label)

    if not grouped:
        return "<p>No Progression account or balance data was available in this run.</p>"

    rows: list[str] = []
    milestone_values = sorted({key[0] for key in grouped})
    for milestone_value in milestone_values:
        milestone_label = "Not captured" if milestone_value == 10**9 else str(milestone_value)
        rows.append(
            "<tr><td colspan='5' style='padding:8px;background:#f1f5f9;border-top:1px solid #dbe3ec'>"
            f"<strong>Next milestone {html.escape(milestone_label)}</strong></td></tr>"
        )
        for key in sorted(k for k in grouped if k[0] == milestone_value):
            _, actual, minimum, remaining, pp_status, balance_status, freshness = key
            actual_text = "—" if actual == 10**9 else str(actual)
            minimum_text = "—" if minimum == 10**9 else str(minimum)
            milestone_text = "—" if milestone_value == 10**9 else str(milestone_value)
            remaining_text = "—" if remaining == 10**9 else str(remaining)
            if balance_status == "pass":
                if actual != 10**9 and minimum != 10**9 and actual > minimum:
                    balance_text = "Above date minimum"
                else:
                    balance_text = "At date minimum"
            else:
                balance_text = {
                    "below_minimum": "Below date minimum",
                    "counter_regressed": "Counter regressed",
                    "pending_baseline": "Pending baseline",
                    "not_validated": "Not validated",
                    "status_not_retained": "Status not retained",
                }.get(balance_status, balance_status.replace("_", " ").title())
            account_names = ", ".join(html.escape(name) for name in sorted(grouped[key]))
            rows.append(
                "<tr>"
                f"<td>{actual_text}</td><td>{minimum_text}</td><td>{milestone_text}</td>"
                f"<td>{remaining_text} to go</td><td><strong>{html.escape(pp_status)} · {len(grouped[key])}</strong>"
                f"<br><small>{html.escape(balance_text)} · {html.escape(freshness)}</small>"
                f"<br><small>{account_names}</small></td></tr>"
            )
    return (
        "<div class='table-wrap'><table class='milestone-table' style='width:100%;min-width:0;border-collapse:collapse;table-layout:fixed'>"
        "<thead><tr><th>Grenades</th><th>Date minimum</th><th>Next milestone</th>"
        "<th>To go</th><th>PP status and accounts</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></div>"
        "<p class='reason'>Pending accounts need another safe inspection. An empty scan is not proof that the full reward inventory has no claimable reward.</p>"
    )


def build_copyable_report(summary: dict[str, Any]) -> str:
    """Render a standalone report; only masked account suffixes are accepted."""
    if not isinstance(summary, dict):
        raise TypeError("summary must be a dictionary")

    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    players = summary.get("player_results") if isinstance(summary.get("player_results"), list) else []
    accounts = _number(summary.get("accounts_processed"))
    configured = _number(summary.get("configured_player_count"))
    claims = _number(totals.get("all_claims_this_run", summary.get("claims_made")))
    attention = sum(
        1 for player in players
        if isinstance(player, dict)
        and not _deferred_progression_is_only_issue(player)
        and (
            bool(player.get("retry_required"))
            or str(player.get("status") or "") in {
                "Partial", "No Rewards", "Login Failed", "Error", "Failed"
            }
            or bool(player.get("daily_unconfirmed"))
            or bool(player.get("standard_store_unconfirmed_indices"))
            or bool(player.get("progression_unconfirmed"))
            or (
                isinstance(player.get("grenade_balance_carry_forward"), dict)
                and str(player["grenade_balance_carry_forward"].get("status_at_observation") or "")
                    in {"below_minimum", "counter_regressed"}
            )
        )
    )
    if not players:
        attention = (
            _number(summary.get("partial_results"))
            + _number(summary.get("actual_failures"))
            + _number(summary.get("unconfirmed_claims"))
        )

    reconciliation = summary.get("reconciliation") if isinstance(summary.get("reconciliation"), dict) else {}
    reconciliation_warnings = reconciliation.get("warnings") if isinstance(reconciliation.get("warnings"), list) else []
    if reconciliation.get("status") in {"attention_required", "partial"}:
        attention = max(attention, len(reconciliation_warnings), 1)

    grenade_validation = (
        reconciliation.get("grenade_balance_validation")
        if isinstance(reconciliation.get("grenade_balance_validation"), dict) else {}
    )
    grenade_status = str(grenade_validation.get("status") or "not_available")
    grenade_table = _progression_milestone_table(players)
    grenade_summary = (
        "<p>Status: <strong>" + _safe(grenade_status.replace("_", " "))
        + "</strong> · Accounts observed: <strong>" + str(_number(grenade_validation.get("accounts_observed")))
        + "</strong> · Below minimum: <strong>" + str(_number(grenade_validation.get("accounts_below_minimum")))
        + "</strong> · Pending baseline: <strong>" + str(_number(grenade_validation.get("accounts_pending_baseline")))
        + "</strong></p>"
    )
    grenade_section = grenade_summary + grenade_table

    # Saved readings are folded into the milestone groups above; do not repeat them in a second table.
    carry_forward_section = ""

    deferred_count = sum(
        1 for player in players
        if isinstance(player, dict) and player.get("progression_deferred")
    )
    deferred_section = (
        "<section class='section'><h2>Progression / Grenade balance — grouped by milestone</h2>"
        + grenade_summary + grenade_table + "</section>"
        if players else ""
    )
    attention_note = (
        "Claim / validation exceptions only; pending Progression accounts are grouped by milestone below"
        if deferred_count else "Accounts with claim / validation exceptions"
    )

    if attention:
        health = "ATTENTION REQUIRED"
        health_class = "warn"
    elif deferred_count:
        health = "COMPLETED — INSPECTIONS PENDING"
        health_class = "neutral"
    else:
        health = "HEALTHY"
        health_class = "healthy"

    categories = [
        ("Daily", totals.get("daily_claimed_this_run", 0)),
        ("Standard Store", totals.get("standard_store_claimed_this_run", 0)),
        ("Promotions", totals.get("promotional_claims_this_run", 0)),
        ("Event rewards", totals.get("event_reward_claims_this_run", 0)),
        ("Progression", totals.get("progression_claimed_this_run", 0)),
        ("Loyalty", totals.get("loyalty_claimed_this_run", 0)),
    ]
    category_cards = "".join(
        "<div class='category'><span>" + html.escape(label) + "</span><strong>"
        + str(_number(value)) + "</strong></div>"
        for label, value in categories
    )

    player_rows = []
    for player in players:
        if not isinstance(player, dict):
            continue
        status = str(player.get("status") or "Unknown")
        if _deferred_progression_is_only_issue(player):
            status = "Deferred check"
        status_class = (
            "bad" if status in {"Login Failed", "Error", "Failed"}
            else "warn" if status in {"Partial", "No Rewards", "Deferred check"} or player.get("retry_required")
            else "good"
        )
        reason = str(player.get("fail_reason") or "").strip()
        details = (
            "<small class='reason'>" + _safe(reason) + "</small>"
            if reason and not _deferred_progression_is_only_issue(player) else ""
        )
        player_rows.append(
            "<tr><td><strong>" + _safe(_player_label(player)) + "</strong>"
            + details + "</td><td><span class='status " + status_class + "'>"
            + _safe(status) + "</span></td><td>" + str(_number(player.get("daily_claimed_this_run")))
            + "</td><td>" + str(_number(player.get("standard_store_claimed_this_run")))
            + "</td><td>" + str(_number(player.get("progression_claimed_this_run")))
            + "</td><td>" + str(_number(player.get("loyalty_claimed_this_run")))
            + "</td><td>" + str(_number(player.get("event_reward_claims_this_run")))
            + "</td></tr>"
        )

    if player_rows:
        table_body = "".join(player_rows)
    else:
        table_body = "<tr><td colspan='7'>No per-account detail was included in this summary.</td></tr>"

    started = _safe(summary.get("started_at_ist"))
    finished = _safe(summary.get("finished_at_ist"))
    run_label = _safe(summary.get("run_label"), "Reward Run")
    version = _safe(summary.get("version"), "unknown")
    email_state = "Sent" if summary.get("email_sent") else "Not sent"
    efficiency = summary.get("full_claim_efficiency")
    efficiency_text = f"{_number(efficiency)}%" if efficiency is not None else "—"
    cycle_health_html = render_cycle_health_overview(summary.get("cycle_health_overview"))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CS Rewards Bot — {_safe(summary.get("run_label"), "Run Report")}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;padding:16px;background:#f1f5f9;color:#172033;font-family:Arial,Helvetica,sans-serif}}
.report-shell{{max-width:1000px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:16px;overflow:hidden}}
.hero{{padding:22px;background:#17365d;color:#fff}}
.brand{{font-size:11px;letter-spacing:1.3px;text-transform:uppercase;opacity:.82}}
h1{{font-size:24px;margin:8px 0}}
.meta{{font-size:12px;line-height:1.6;color:#dbeafe;overflow-wrap:anywhere}}
.health{{display:inline-block;margin-top:12px;padding:7px 12px;border-radius:18px;font-size:11px;font-weight:700}}
.health.healthy{{color:#166534;background:#dcfce7}}
.health.warn{{color:#92400e;background:#fef3c7}}
.health.neutral{{color:#1e40af;background:#dbeafe}}
.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:14px 18px;background:#f8fafc;border-bottom:1px solid #e2e8f0}}
button{{border:0;border-radius:8px;padding:11px 16px;background:#1d4ed8;color:#fff;font-weight:700;font-size:13px;cursor:pointer}}
button:focus{{outline:3px solid #93c5fd;outline-offset:2px}}
.copy-status{{font-size:12px;color:#475569}}
.content{{padding:18px}}
.kpis{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:14px}}
.kpi,.category,.section{{border:1px solid #e2e8f0;border-radius:10px;background:#fff;padding:13px;min-width:0}}
.kpi span,.category span{{display:block;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.6px}}
.kpi strong{{display:block;margin:6px 0;font-size:26px}}
.kpi small{{color:#64748b;font-size:11px}}
.categories{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:14px}}
.category strong{{display:block;font-size:20px;margin-top:7px}}
.section{{margin-bottom:14px;padding:15px}}
h2{{font-size:15px;margin:0 0 12px}}
.table-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px;min-width:720px}}
.cycle-health-table{{min-width:0 !important;width:100%;table-layout:fixed}}
.cycle-health-table td{{overflow-wrap:anywhere}}
th,td{{padding:10px 8px;text-align:left;border-bottom:1px solid #e5e7eb;vertical-align:top}}
th{{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;background:#f8fafc}}
.status{{display:inline-block;padding:4px 7px;border-radius:12px;font-size:10px;font-weight:700;white-space:nowrap}}
.status.good{{color:#166534;background:#dcfce7}}
.status.warn{{color:#92400e;background:#fef3c7}}
.status.bad{{color:#991b1b;background:#fee2e2}}
 .reason{{display:block;max-width:260px;margin-top:4px;color:#92400e;line-height:1.4;overflow-wrap:anywhere}}
.deferred{{background:#fffbeb;border-color:#f4d2a4}}
.deferred-item{{padding:10px 0;border-top:1px solid #fed7aa}}
.deferred-item strong{{display:block;font-size:12px}}
.deferred-item span{{display:block;margin-top:3px;color:#64748b;font-size:10px}}
.deferred-item p{{margin:6px 0 0;font-size:11px;line-height:1.4}}
.footer{{padding:14px 18px;color:#64748b;font-size:10px;text-align:center;border-top:1px solid #e2e8f0}}
@media(max-width:620px){{
 body{{padding:7px}}
 .hero{{padding:17px}}
 h1{{font-size:20px}}
 .content{{padding:11px}}
 .toolbar{{padding:11px}}
 .kpis{{grid-template-columns:1fr 1fr}}
 .kpi:first-child{{grid-column:span 2}}
 .categories{{grid-template-columns:1fr 1fr}}
 .section{{padding:11px}}
}}
@media print{{
 body{{padding:0;background:#fff}}
 .report-shell{{border:0;border-radius:0}}
 .toolbar{{display:none}}
}}
</style>
</head>
<body>
<main class="report-shell" id="report">
<header class="hero">
<div class="brand">CS Rewards Bot · Run Report</div>
<h1>Rewards Summary</h1>
<div class="meta">{run_label}<br>Started: {started}<br>Finished: {finished}<br>Version: {version}</div>
<span class="health {health_class}">{health}</span>
</header>
<div class="toolbar">
<button type="button" id="copy-report">Copy Report</button>
<span class="copy-status" id="copy-status" role="status" aria-live="polite">Copies the formatted report where rich clipboard is supported.</span>
</div>
<div class="content" id="report-content">
<section class="kpis">
<div class="kpi"><span>Confirmed claims</span><strong>{claims}</strong><small>Portal-confirmed during this run</small></div>
<div class="kpi"><span>Accounts processed</span><strong>{accounts}/{configured}</strong><small>Configured roster coverage</small></div>
<div class="kpi"><span>Need attention</span><strong>{attention}</strong><small>{attention_note}</small></div>
</section>
{cycle_health_html}
<section class="categories">{category_cards}</section>
{deferred_section}
{carry_forward_section}
<section class="section">
<h2>Account results</h2>
<div class="table-wrap">
<table>
<thead><tr><th>Account</th><th>Status</th><th>Daily</th><th>Store</th><th>Progression</th><th>Loyalty</th><th>Events</th></tr></thead>
<tbody>{table_body}</tbody>
</table>
</div>
</section>
<section class="section">
<h2>Run checks</h2>
<div>Full-claim efficiency: <strong>{efficiency_text}</strong></div>
<div>Actual failures: <strong>{_number(summary.get("actual_failures"))}</strong></div>
<div>Unconfirmed claims: <strong>{_number(summary.get("unconfirmed_claims"))}</strong></div>
<div>Retry candidates: <strong>{_number(summary.get("retry_candidate_count"))}</strong></div>
<div>Email delivery: <strong>{email_state}</strong></div>
</section>
<section class="section">
<h2>Reward reconciliation</h2>
<div>Status: <strong>{_safe(reconciliation.get("status"), "Not available")}</strong></div>
<div>Per-account claim sum: <strong>{_number((reconciliation.get("claim_counts") or {}).get("per_account_sum"))}</strong></div>
<div>Category total: <strong>{_number((reconciliation.get("claim_counts") or {}).get("category_sum"))}</strong></div>
<div>Recorded claim counter: <strong>{_number((reconciliation.get("claim_counts") or {}).get("recorded_claim_counter"))}</strong></div>
{"<ul>" + "".join("<li>" + _safe(item) + "</li>" for item in reconciliation_warnings) + "</ul>" if reconciliation_warnings else "<p>All available arithmetic and roster-scope checks matched.</p>"}
</section>
</div>
<footer class="footer">CS Rewards Bot · Automated report · Player IDs are masked to their last four characters.</footer>
</main>
<script>
(() => {{
  const button = document.getElementById('copy-report');
  const status = document.getElementById('copy-status');
  if (!button || !status) return;

  const renderReportPng = reportNode => new Promise((resolve, reject) => {{
    try {{
      const clone = reportNode.cloneNode(true);
      const toolbar = clone.querySelector('.toolbar');
      if (toolbar) toolbar.remove();
      const css = Array.from(document.querySelectorAll('style'))
        .map(style => style.textContent || '').join('\\n');
      const width = Math.max(320, Math.ceil(reportNode.scrollWidth));
      const height = Math.max(200, Math.ceil(reportNode.scrollHeight));
      const markup = '<div xmlns="http://www.w3.org/1999/xhtml" style="width:' + width
        + 'px;background:#fff"><style>' + css + '</style>' + clone.outerHTML + '</div>';
      const svg = '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xhtml="http://www.w3.org/1999/xhtml"'
        + ' width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '">'
        + '<foreignObject x="0" y="0" width="100%" height="100%">' + markup + '</foreignObject></svg>';
      const url = URL.createObjectURL(new Blob([svg], {{type:'image/svg+xml;charset=utf-8'}}));
      const image = new Image();
      image.onload = () => {{
        try {{
          const scale = 2;
          const canvas = document.createElement('canvas');
          canvas.width = width * scale;
          canvas.height = height * scale;
          const context = canvas.getContext('2d');
          if (!context) throw new Error('Canvas context unavailable.');
          context.scale(scale, scale);
          context.drawImage(image, 0, 0, width, height);
          URL.revokeObjectURL(url);
          canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('PNG encoding failed.')), 'image/png');
        }} catch (error) {{
          URL.revokeObjectURL(url);
          reject(error);
        }}
      }};
      image.onerror = () => {{
        URL.revokeObjectURL(url);
        reject(new Error('Report image rendering failed.'));
      }};
      image.src = url;
    }} catch (error) {{
      reject(error);
    }}
  }});

  const copyPlainText = text => {{
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand('copy');
    textarea.remove();
    if (!copied) throw new Error('Text copy command was not accepted.');
  }};

  button.addEventListener('click', async () => {{
    button.disabled = true;
    status.textContent = 'Preparing report image…';
    const reportNode = document.querySelector('.report-shell');
    const contentClone = reportNode.cloneNode(true);
    const toolbar = contentClone.querySelector('.toolbar');
    if (toolbar) toolbar.remove();
    const styles = Array.from(document.querySelectorAll('style'))
      .map(style => style.textContent || '').join('\\n');
    const richHtml = '<!doctype html><html><head><meta charset="utf-8"><style>'
      + styles + '</style></head><body>' + contentClone.outerHTML + '</body></html>';
    const plainText = contentClone.innerText.trim();

    try {{
      if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {{
        try {{
          const pngBlob = await renderReportPng(reportNode);
          const item = new ClipboardItem({{
            'image/png': pngBlob,
            'text/html': new Blob([richHtml], {{type:'text/html'}}),
            'text/plain': new Blob([plainText], {{type:'text/plain'}})
          }});
          await navigator.clipboard.write([item]);
          status.textContent = 'Report copied as an image.';
          return;
        }} catch (imageError) {{
          status.textContent = 'Image copy unavailable; trying formatted report…';
          const htmlItem = new ClipboardItem({{
            'text/html': new Blob([richHtml], {{type:'text/html'}}),
            'text/plain': new Blob([plainText], {{type:'text/plain'}})
          }});
          await navigator.clipboard.write([htmlItem]);
          status.textContent = 'Report copied with formatting (image copy was unavailable).';
          return;
        }}
      }}
      throw new Error('Rich clipboard is not available in this browser context.');
    }} catch (richError) {{
      try {{
        copyPlainText(plainText);
        status.textContent = 'Report copied as plain text (rich copy was unavailable).';
      }} catch (plainError) {{
        status.textContent = 'Clipboard access was blocked. Select the report and press Ctrl+C.';
      }}
    }} finally {{
      button.disabled = false;
    }}
  }});
}})();
</script>
</body>
</html>
"""
