# Loom script (5–10 min) — honest, greppable, no stage tricks

Rippling grades: **does it work / accurate**, **real agentic loop**, **tool
usage (ad libraries first)**, **output quality + Rippling-relevance**, and
**decisions & trade-offs** (this Loom). The rule for this script: *every claim I
make on camera has a grep or a URL the reviewer could run themselves.* I lead
with what's real and I own the gaps honestly — that's the whole pitch.

**Before recording:** `make api` on :8000, UI on :5173 with the live Deel run
`RUN-b256fab1c1dd` selected. Frozen reference copy in `docs/reference_scenario/`.

---

## 0. Framing (30s)

> "This is a conversational research agent. Give it any competitor — name or
> domain — and it produces an evidence-grounded read of their public marketing:
> how they position now, how it changed, how it compares to Rippling, and what
> defensible campaigns that surfaces. It's not hardcoded to one company — the
> focal company and taxonomy are config. The design bias throughout is: *show
> the evidence, or don't make the claim.*"

## 1. Architecture — a real agentic loop, not a script (90s)

> **Before recording — pick the demo run.** The polished flagship
> `RUN-b256fab1c1dd` was collected *before* the LLM-in-loop landed, so its trace
> uses the deterministic scorer. For the `decision_by: model` beat, grep a run
> created *after* it — the best move is to **re-run `deel.com` live once** (needs
> the Anthropic usage limit raised — see the handoff note) so ONE run has
> everything: model decisions, real ad creatives, and the full corpus. If you
> can't re-run live, grep `decision_by` on any post-loop run and show the
> polished outputs on the flagship, noting the flagship predates the loop.

- Open the demo run's `trace.jsonl`. Scroll the `event_type` stream:
  `coverage_assessed → actions_proposed → action_selected`. *"It assesses what
  it knows, proposes candidate actions, and picks the next one."*
- **The key beat — the model is in the loop.** `grep '"decision_by"'
  trace.jsonl`: *"the next-action decision isn't a hardcoded score. A reasoning
  model gets the coverage state, the scored candidates, and what just
  succeeded or came back empty, and picks the next action with a written
  rationale — `decision_by: model`. When the model is unavailable it falls back
  to the deterministic scorer, `decision_by: heuristic`. Two different
  competitors genuinely diverge — verified live on workday vs deel vs gusto:
  the model chose news/wayback/comparison-page pulls the heuristic wouldn't."*
- `grep tool_failed` / `grep source_skipped`: *"a provider failure or empty
  source is a typed result, not a crash — recorded, and the loop reacts to it."*
- `grep stop_selected`: *"it stops for an explainable reason and the brief says
  exactly which — required coverage, or budget/runtime-bounded with what wasn't
  attempted disclosed."*
- **Why this structure:** a checkpointed 25-node state machine (`graph.py` +
  `nodes.py`) over a `DirectorState` — killable and resumable at any node. A
  node crash is recorded `status='failed'` with an honest error brief; it is
  never reported "complete." (`docs/SYSTEMS_GUIDE.md` §2.)

## 2. Tool & model choices (45s — assignment-required)

> "Model routing (`config/model_routes.yaml`): high-volume bounded
> classification runs on **Haiku** — fast, cheap, schema-forced. The reasoning
> tier (**Sonnet**) does the things that need judgment: the in-loop planner,
> temporal judgment, claim judging, opportunity generation + critique, and the
> chat. Tools: **Exa** for retrieval only (search, domain-scoped discovery,
> date-windowed prior sampling, LinkedIn post extraction, the Similarweb
> partner) — every Exa result is re-classified and verbatim-verified by our own
> pipeline. **Wayback CDX** gives real capture dates. **SerpApi** gives real
> Google ad creatives (next section). Each tool is one `BaseTool` contract that
> turns every failure into a typed result — that's the graceful-degradation
> story."

## 3. Tool usage — real ad creatives (60s — ad libraries are listed first)

- **Where to win / Demand tab → the ads section.** *"The assignment lists ad
  libraries first, so this had to be real. We pull live Google ad creatives
  from the Ads Transparency Center via SerpApi: verified advertiser, format,
  run dates, and the actual copy on video ads."* Show a real Deel creative and
  its Transparency permalink — the reviewer can open it.
- **Own the honesty boundary out loud:** *"Text and image creatives are
  rendered images — the copy is baked into the pixels, so we mark them 'copy not
  machine-readable' rather than OCR-guess. And Google shows no spend, CPC, or
  impressions for commercial ads — so we claim none. Meta needs a developer
  token I didn't set up, so Meta is an honest typed skip, not fake data."* This
  is the trade-off the rubric rewards.

## 4. Accuracy is enforced by the application (60s)

- **Evidence & trust tab → Claims ledger.** Expand a claim: status, why we hold
  this confidence, alternative explanations, verbatim evidence rows. *"Grounding
  gate on this run: every material claim cited, zero broken evidence refs. If
  the model quotes something not in the page, the app drops it at extraction —
  the application is the accuracy gate, not the model."*
- **Data honesty card:** what we attempted and found nothing, what failed, what
  we excluded. *"Absence is a finding, never hidden. ROAS/CAC/spend are banned —
  we never estimate what isn't public."*

## 5. Output quality + Rippling-relevance (75s)

- **Overview → the bottom line + scorecard:** *"composed only from verified
  numbers — the ownership split, what's moving, how many repeated claims are
  clean attack openings, the top play with a THIN caveat when it rests on few
  pages."* Corpus sizes and the stop reason in the trust-envelope line.
- **The required brief section** (`brief.md` → "The assignment deliverable"):
  the four questions answered from the evidence — messaging themes, product
  positioning, what changed, and **the gaps/plays for Rippling** — every row
  cited to a source artifact. *"This is the deliverable, and every claim in it
  is one click from its evidence."*
- **Download bar** at the top of the run: the markdown brief and the JSON
  package (sources, claims, confidence, timestamps) — the two required outputs.

## 6. Conversational — the chat is the interface (75s)

- **Ask the chat a question it CAN answer** → grounded rich-text answer with
  source chips.
- **The generative beat:** ask for a chart — *"the chat builds new graphs and
  tables on demand. The model chooses what to chart; Python computes every
  number from the real package, so a fabricated chart can't reach the screen."*
- **Ask something the data CAN'T answer** → *"it doesn't bluff. It says what's
  missing and offers to go get it — a scoped research request. One click
  re-drives the SAME run scoped to those sources, appends evidence, re-verifies,
  and the tabs grow in place."* Show the "✦ Ask AI" panel opening from a section
  with that element's context.

## 7. Eval system (45s — assignment-required)

> "Layered, and honest about which layers are objective. Schema/excerpt validity
> and the grounding gate are objective and final — they verify the pipeline's
> own invariants and fail the build on a violation. Classification quality is
> inter-model agreement — an independent Sonnet labeler vs. the production Haiku
> classifier — and I label it **provisional, pending human adjudication**; I
> don't ship an accuracy number I haven't earned. A known measurement bug (a
> segment-vocabulary mismatch) is logged in `docs/eval_harness_issues.md`, not
> silently fixed. And the whole thing was hardened by an independent red-team
> loop over a **frozen** test surface — grades only move when the product
> actually improves (`docs/improvement_log.md`)."

## 8. Live demo — point it at a fresh competitor (60s)

- Run one live from the UI (or `competitive-agent analyze <domain> --compare
  rippling.com --execution-mode live`). Watch the live strip stream sources,
  themes, and the agent's own decisions. *"Refresh the page — it's still there;
  the run survives restarts."*
- If short on time, open a second finished run (Gusto/Vanta) and show the
  research path is different — the loop adapted to that competitor's industry.

## Close (20s)

> "Everything you saw is reproducible from a stored run, runs locally, and works
> for any competitor at any size. Where I have gaps — Meta ads, image-ad OCR,
> human-adjudicated accuracy — they're disclosed in the product, not hidden.
> That's the bar I held: show the evidence, or don't make the claim."

---

## Anticipated Q&A — see `docs/loom_talking_points.md` for the full set

Short list of the ones a sharp reviewer will ask: *how do I know the numbers
aren't hallucinated* (grounding gate + verbatim containment + deterministic
recompute) · *what did the model decide vs the code* (model: bounded per-item +
next-action; code: everything numeric) · *what about a tiny competitor*
(corpus-size normalization) · *why is Meta empty* (no dev token — honest skip) ·
*is the eval real* (objective layers final, classification provisional) · *what
breaks it* (JS-only sites, ad image OCR, thin prior windows).
