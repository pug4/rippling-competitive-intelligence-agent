# Loom walkthrough script (~7–9 min)

A tight demo of the Competitive Marketing Intelligence Agent. Lead with
accuracy and the agentic loop, then the UI — it now carries the analysis depth.
Every claim shown on screen resolves to evidence — that is the point.

**Before recording:** `make api` running on :8000, UI open on :5173 with the
flagship run `RUN-b256fab1c1dd` (deel.com vs rippling.com, live) selected.
A frozen copy of the same run lives in `docs/reference_scenario/` if you want
a file to open instead.

## 0. Framing (30s)

> "This is a conversational research agent. You give it a competitor — any
> company, any industry — and it produces an evidence-grounded read of their
> public marketing strategy: how they position now, how that changed over time,
> how it compares to a focal company (here, Rippling), and what defensible
> marketing actions that surfaces. Rippling is just config — the focal company
> and the taxonomy are swappable, and comparisons are corpus-size-normalized so
> it works against a 12-page niche competitor as honestly as against Deel."

## 1. It's a real agentic loop, not a script (75s)

- Open `outputs/runs/RUN-b256fab1c1dd/trace.jsonl`. Scroll the `event_type`
  stream: `coverage_assessed` → `actions_proposed` → `action_selected`.
  *"It assesses what it knows, proposes actions, scores them, and records the
  rationale AND the alternatives it didn't pick — 19 of these cycles."*
- `grep tool_failed` and `grep source_skipped`: *"A provider failure or an
  empty source is a typed result, not a crash — it's recorded and the loop
  keeps going."* (Do NOT grep `fallback_selected` — it does not occur in this
  trace.)
- `grep stop_selected`: *"It stops for an explainable reason. This run ran to
  its research-time budget — and the report says exactly that: 'budget-bounded
  (hit the research-time cap)', with what remained unattempted disclosed as a
  limitation."*

## 1b. Tool & model choices (30s — assignment-required beat)

> "Model routing: high-volume bounded classification runs on **Haiku** (fast,
> cheap, schema-forced); temporal judgment, claims, opportunity generation, and
> the chat run on the reasoning tier — reasoning stays in Claude. **Exa** is
> used strictly for retrieval: neural search, domain-scoped discovery,
> published-date-windowed sampling, the Agent for per-post LinkedIn extraction,
> and the Similarweb enrichment — every Exa result is re-classified and
> verbatim-verified by our own pipeline. **Wayback CDX** gives real capture
> timestamps for temporal claims. **Meta/LinkedIn ad libraries** have no public
> commercial API — we record a typed, honest skip rather than fake data."

## 2. Accuracy is enforced by the application, not trusted from the model (75s)

- UI → **Sources & evidence** tab → **Claims ledger (20)**: expand one claim.
  *"Every strategic claim carries its status, why we hold this confidence,
  alternative explanations, and the verbatim evidence rows behind it. The
  grounding gate on this run: 20 of 20 material claims cited, zero broken
  evidence references. If the model quotes something that isn't in the page,
  the app drops it — the application is the accuracy gate."*
- Scroll to the **Data honesty** card: *"What we attempted and found nothing,
  what failed, what we excluded — 4 junk ad-transparency pages were excluded at
  render, and it says so. Absence is a finding, never hidden."*
- *"ROAS, CAC, spend — banned. We never estimate what isn't public."*

## 3. Depth: bottom line, scorecard, messaging popups, LinkedIn, demand (100s)

- **Overview — start here**: read **"The bottom line"** aloud — *"this sentence
  is composed only from the run's verified numbers: the ownership split, what's
  moving, how many claims are clean attacks, and the top play — with a THIN
  caveat when the play rests on few pages."* Then the **strategic scorecard**:
  who owns the buying intents, message-investment deltas (CLOSE/PRESS tags),
  momentum (**NEW / SHIFTED / STABLE** — count-based 'expanding' is
  deliberately excluded because asymmetric windows guarantee raw growth), and
  the attack surface. Point at the small **trust envelope** line: corpus sizes,
  budget-bounded stop, what wasn't attempted.
- **HOVER anything** — this is a headline beat: *"hover any classification —
  a theme bar, a map dot, a buying trigger — and you get their ACTUAL verbatim
  messaging with the source it came from. Click a LinkedIn theme and the real
  posts open. No claim is more than one hover from its evidence."*
- **Where to win** tab: the **attack/defend map** — numbered dots colored by
  the recommended stance, quadrant chips, a legend with THIN n=X flags where a
  verdict rests on too few pages; then gaps with ACTION tags and the Action
  Board — every play has metric, guardrails, staged proceed/stop gates, kill
  rule.
- **LinkedIn signals** tab: *"30 individual employee posts — including the CEO
  — each classified for theme, stance, and product vertical; click a theme to
  read the actual posts."* (Collection is a toggle on new runs — on by
  default, off to save Exa credits.)
- **Demand & channels** tab: **search-intent ownership, share-normalized** —
  *"Raw counts lie across corpus sizes. A 79-vs-16 page blowout is 'Deel
  advantage' at a 4.6× share ratio; and where the sample is too thin to call,
  it says 'insufficient sample' instead of asserting ownership — 12 of 23
  triggers here."* Plus proof-vs-voice, the open-SERP affinity view, and
  traffic estimates (labeled estimated).
- **Paid-search targets** (same tab, section 4): *"It goes from analysis to
  media plan: keyword clusters drafted ONLY from observed evidence — their
  buying triggers, their verbatim villain wording, their live ad creatives.
  Volumes and CPCs aren't publicly knowable, so it never invents them: every
  cluster ships 'validate before spend', competitor-brand bidding is
  auto-flagged for legal review, and any quote the model can't prove verbatim
  demotes its cluster to 'inferred'. On this run: 9 of 10 clusters
  quote-verified."*
- Ask the grounded chat one question on camera (bottom of Overview — e.g.
  *"Which buying triggers does Deel own vs Rippling after normalizing for
  corpus size?"*): it answers in rich text from this run's evidence only,
  shows the sources it grounded in as chips, and can scope to one product
  vertical with the Focus selector.

## 4. Change over time — honestly, with YOUR windows (60s)

- **Changes over time** tab: *"The prior-window baseline shows what WAS there —
  14 dated artifacts. Change events are detected mid-run and then RECONCILED
  against the final corpus, so the system can never claim a theme 'emerged'
  that its own baseline saw earlier: three of the five signals here were
  auto-relabeled from 'emerging' to 'expanding — present but rare in the prior
  window (e.g. 4 of 14)'. Every event keeps the coverage-asymmetry caveat —
  absence in the archive is not absence in the world."*
- **Customize the windows live**: change History to 6 months / Recent to 30
  days and hit Apply — *"the whole temporal story recounts instantly from the
  already-collected corpus, deterministically, no model calls — and the banner
  says the saved report keeps the run's original windows. Use it to test
  whether a 'change' survives moving the line."* Reset afterwards.

## 5. Rippling-relative actions (45s)

- Brief `## Action Board` (or the dashboard's opening section): *"Ordered for
  Rippling, tagged P1/P2/P3. Each action carries proof status, backfire risk,
  staged proceed/stop gates, and a kill rule."*
- **What not to attack**: *"Just as important — where attacking would backfire."*

## 6. Conversation, portfolio, honesty (45s)

- `competitive-agent ask <RUN> "why do you believe this?"` → answers from
  stored evidence. `challenge`/`retry` create **child runs** with a diff and
  reuse the parent's evidence non-destructively.
- `competitive-agent portfolio deel.com gusto.com workday.com` → isolated
  pipelines, provably no cross-company leakage. Modes (snapshot, comparative,
  longitudinal) run concurrently — the UI queues them as background jobs.
- **Runs are durable and stream live** (optional beat — start a new run to
  show it): *"Queue a competitor and watch it work: sources, themes, and the
  agent's own decisions stream into the UI from its database checkpoints.
  Refresh the page — still there. Kill the server mid-run — it comes back as
  'interrupted' with everything saved, and Resume continues from the last
  checkpoint without re-fetching or re-paying for anything."*
- Benchmark: *"Grounding and validity are objective and final. Classification
  accuracy is human-adjudicated on a held-out set — we don't ship an accuracy
  number we haven't earned."*
- Optional niche-competitor beat: switch to the **RemoFirst vs Rippling** live
  run — *"75 pages vs 116: everything stays share-normalized, and notice the
  system flags its own #1 recommended play as THIN (3 competitor pages) and
  tells you to verify before spending. It would rather caveat itself than
  oversell."*

## Close (15s)

> "Everything you saw is reproducible from a stored run, runs locally, and
> works for any competitor at any size — the design bias throughout is: show
> the evidence, or don't make the claim."
