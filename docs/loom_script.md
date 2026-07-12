# Loom walkthrough script (~6–8 min)

A tight demo of the Competitive Marketing Intelligence Director. Lead with
accuracy and the agentic loop; treat the UI as a bonus. Every claim shown on
screen resolves to evidence — that is the point.

## 0. Framing (30s)

> "This is a conversational research agent. You give it a competitor — any
> company, any industry — and it produces an evidence-grounded read of their
> public marketing strategy: how they position now, how that changed over time,
> how it compares to a focal company (here, Rippling), and what defensible
> marketing actions that surfaces. Rippling is just config — the focal company,
> the competitor list, and the taxonomy are all swappable."

## 1. It's a real agentic loop, not a script (90s)

- Open `outputs/runs/<REF_RUN>/trace.jsonl`. Scroll the `event_type` stream.
- Point at a `coverage_assessed` → `actions_proposed` → `action_selected`
  sequence: *"It assesses what it knows, proposes actions, scores them, and
  records the rationale and the alternatives it didn't pick."*
- Show one `fallback_selected` and one `tool_failed`→recovery: *"A provider
  failure is a typed result, not a crash — it falls back and keeps going."*
- Show `stop_selected` with its reason: *"It stops for an explainable reason —
  here, required coverage reached — not a fixed step count."*

## 1b. Tool & model choices (30s — assignment-required beat)

> "Model routing: high-volume bounded classification runs on **Haiku** (fast,
> cheap, schema-forced); planning, temporal judgment, opportunity generation,
> and the chat run on **Sonnet-tier** — reasoning stays in Claude. **Exa** is
> used strictly for what it's best at — retrieval: neural search, domain-scoped
> discovery, published-date-windowed sampling, and the Agent for LinkedIn post
> extraction — every Exa result is re-classified and verbatim-verified by our
> own pipeline. **Wayback CDX** gives real capture timestamps for temporal
> claims. **Meta/LinkedIn ad libraries** have no public commercial API — we
> propose the attempt and record a typed, honest skip rather than fake data
> (see docs/adr/0002-model-routing.md and provider_notes.md)."

## 2. Accuracy is enforced by the application, not trusted from the model (90s)

- Open `brief.md`, scroll to the **Evidence appendix**. Every quoted excerpt is
  re-verified verbatim against stored source text.
- Show the run log line `dropped unverifiable excerpt…`: *"If the model quotes
  something that isn't in the page, we drop it. The app is the accuracy gate."*
- Point at a rejected claim / the "not publicly knowable" rule: *"ROAS, CAC,
  spend — rejected. We never estimate what isn't public."*

## 3. Current standing — the depth (60s)

- `brief.md` → **Current public positioning**: dominant message chosen by
  authority × salience (homepage/platform pages outrank a blog post), the
  villain wording verbatim, the stance distribution, proof distribution.
- **Message–proof gaps**: *"Where the competitor makes a claim they can't back —
  and whether Rippling can."*

## 4. Change over time — honestly (45s)

- **Strategy over time**: *"A change is only asserted when there's evidence in
  BOTH periods, with real Wayback capture dates. Emerging themes are marked low
  confidence, and we always add the coverage-asymmetry caveat — absence in the
  archive is not absence in the world."*

## 5. Rippling-relative actions (60s)

- **Action Board** (first thing in the brief): *"Ordered for Rippling. Each
  action carries an experiment hypothesis, scale/iterate/kill rules, and a
  backfire risk."*
- **What not to attack**: *"Just as important — where attacking would backfire
  because Rippling is exposed on the same axis."*

## 6. Conversation, portfolio, honesty (60s)

- `competitive-agent ask <RUN> "why do you believe this?"` → answers from stored
  evidence. `deepen`/`challenge`/`retry` create **child runs** with a diff and
  reuse the parent's evidence non-destructively — the original is never lost.
- `competitive-agent portfolio deel.com gusto.com workday.com` → isolated
  pipelines, and it proves **no cross-company leakage** before synthesizing.
- `evals/reports/benchmark_report.md`: *"Grounding and validity are objective
  and final. The classification numbers are inter-model agreement, clearly
  marked provisional until human adjudication — we don't ship an accuracy number
  we haven't earned."*

## 7. Bonus UI (30s)

- `make api` + `make ui-dev` → the React app reads the same validated package:
  Action Board, positioning, coverage, evidence drill-down. *"No separate
  analytics — one source of truth."*

## Close (15s)

> "Everything you saw is reproducible from a stored run, runs locally, and works
> for any competitor. The design bias throughout is: show the evidence, or don't
> make the claim."
