# Alert engine

`app/intelligence/alerts.py` (rules) and
`app/api/v1/endpoints/field_reports.py` (lifecycle transitions).

## The hard problem

Not detecting a bad value — **not crying wolf**. A three-month drought must
produce one alert, not ninety, or operators stop reading them.

Four mechanisms, each answering a specific failure:

| Mechanism | Prevents |
|---|---|
| **persistence** — level must hold N cycles | one noisy composite escalating a district |
| **hysteresis** — de-escalating needs a bigger move | a score on a threshold flapping nightly |
| **cooldown** — 3 days after a resolve | immediate re-raise of the same thing |
| **deduplication** — `dedupe_key` | a new alert every night for an ongoing hazard |

Plus a **confidence floor** (0.35): a score built on thin evidence is reported
but never alerted on. Alerting from a two-component score on a three-year
baseline is noise wearing the costume of a warning.

## Severity

Hazard level maps to alert severity through an explicit table, so a change to
hazard bands cannot silently shift what severity a district alerts at.

| Hazard level | Alert severity |
|---|---|
| WATCH | WATCH |
| MODERATE | ADVISORY |
| SEVERE | WARNING |
| EXTREME / CRITICAL | CRITICAL |
| NORMAL | *(no alert; closes an open one)* |

## Lifecycle

```
OPEN ──► ACKNOWLEDGED ──► INVESTIGATING ──► CONFIRMED ──► RESOLVED
  │            │                 │
  └────────────┴─────────────────┴──────────► DISMISSED
```

Transitions are constrained by a state machine (`ALERT_TRANSITIONS`), not
free-form status writes. A dismissed alert cannot be reopened as
"investigating", and an audit trail is only meaningful if the moves were legal.

`RESOLVED` and `DISMISSED` are terminal.

Every transition writes to **both** `gv_alert_history` (the alert's biography,
rendered in the UI) and `gv_audit_logs` (the security record). Keeping them
apart means the audit log stays a security artifact rather than a UI data
source.

## Alerts explain themselves

Every alert carries `reason`, `rule_id` and `evidence` — the contributors,
their values and the persistence count. Not "Flood detected", but:

> Larkana — flood extent increased 37% over 3 days; surface water covers 32%
> of the district against an 8% seasonal baseline, +12 SD from normal,
> approximately 456 km². Data quality 91%.

## Not official warnings

Every alert carries `is_official_warning: false` and the envelope carries:

> Satellite-derived analytical indicator produced by this system. Not an
> official disaster warning and carries no authority from NDMA, PDMA or any
> government body.

This system has no authority to issue a warning. An academic prototype claiming
otherwise would be worse than useless.

## Field verification

`gv_field_reports` closes the loop. Any signed-in user may submit a ground
observation — a field officer is not an operator, and requiring elevated
privilege to report what you can see would defeat the purpose. Reviewing is
operator-gated.

A **REJECTED** report is the most valuable row in the table: it is the only
mechanism by which the system can learn that a detection rule is wrong. An
automated pipeline with no path for "you got this wrong" cannot be corrected.
