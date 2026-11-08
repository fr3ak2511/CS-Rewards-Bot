"""Self-contained, mobile-first run report with a rich-copy action.

The email remains email-client-safe and script-free. This companion HTML artifact
is intended to be opened in a browser, where its Copy Report button can write
both formatted HTML and plain text to the clipboard.
"""

from __future__ import annotations

import html
from typing import Any


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
        if isinstance(player, dict) and (
            bool(player.get("retry_required"))
            or str(player.get("status") or "") in {
                "Partial", "No Rewards", "Login Failed", "Error", "Failed"
            }
            or bool(player.get("daily_unconfirmed"))
            or bool(player.get("standard_store_unconfirmed_indices"))
            or bool(player.get("progression_unconfirmed"))
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
    if reconciliation.get("status") == "attention_required":
        attention = max(attention, len(reconciliation_warnings), 1)

    health = "ATTENTION REQUIRED" if attention else "HEALTHY"
    health_class = "warn" if attention else "healthy"

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
        status_class = (
            "bad" if status in {"Login Failed", "Error", "Failed"}
            else "warn" if status in {"Partial", "No Rewards"} or player.get("retry_required")
            else "good"
        )
        reason = str(player.get("fail_reason") or "").strip()
        details = (
            "<small class='reason'>" + _safe(reason) + "</small>"
            if reason else ""
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
th,td{{padding:10px 8px;text-align:left;border-bottom:1px solid #e5e7eb;vertical-align:top}}
th{{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;background:#f8fafc}}
.status{{display:inline-block;padding:4px 7px;border-radius:12px;font-size:10px;font-weight:700;white-space:nowrap}}
.status.good{{color:#166534;background:#dcfce7}}
.status.warn{{color:#92400e;background:#fef3c7}}
.status.bad{{color:#991b1b;background:#fee2e2}}
.reason{{display:block;max-width:260px;margin-top:4px;color:#92400e;line-height:1.4;overflow-wrap:anywhere}}
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
<div class="kpi"><span>Need attention</span><strong>{attention}</strong><small>Accounts with unresolved items</small></div>
</section>
<section class="categories">{category_cards}</section>
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

  button.addEventListener('click', async () => {{
    button.disabled = true;
    status.textContent = 'Preparing report…';
    const clone = document.documentElement.cloneNode(true);
    const toolbar = clone.querySelector('.toolbar');
    if (toolbar) toolbar.remove();
    const richHtml = '<!doctype html>' + clone.outerHTML;
    const plainText = document.getElementById('report-content').innerText.trim();

    try {{
      if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {{
        const item = new ClipboardItem({{
          'text/html': new Blob([richHtml], {{type:'text/html'}}),
          'text/plain': new Blob([plainText], {{type:'text/plain'}})
        }});
        await navigator.clipboard.write([item]);
        status.textContent = 'Report copied with formatting.';
        return;
      }}
      throw new Error('Rich clipboard is not available in this browser context.');
    }} catch (richError) {{
      try {{
        const textarea = document.createElement('textarea');
        textarea.value = plainText;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('Text copy command was not accepted.');
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
