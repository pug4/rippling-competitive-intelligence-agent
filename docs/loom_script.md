# Loom script (~8 min) — the four required walk-throughs

The assignment asks the Loom to cover exactly four things:
**(1) architecture decisions, (2) tool & model choices, (3) how the eval works,
(4) a live demo.** This script is organized in that order. The rule for every
claim: it has a grep or a URL a reviewer could run themselves. Lead with what's
real; own the gaps.

**Before recording, have open:**
- Terminal in the repo.
- `make api` on :8000, UI on :5173, with the freshly-completed **Deel vs
  Rippling** run selected (the one with `decision_by: model` in its trace, real
  ad creatives, and LinkedIn posts). A frozen reference copy is in
  `docs/reference_scenario/`.
- `docs/SYSTEMS_GUIDE.md` (the architecture map) in case you want to point at it.

---

## 0. Framing (30s)

> "This is a conversational research agent. You give it any competitor — a name
> or a domain — and it produces an evidence-grounded read of their public
> marketing: how they position now, how it's changed, how it compares to
> Rippling, and what campaigns that surfaces. It works for any competitor —
> Rippling and the taxonomy are just config. The one design bias throughout:
> *show the evidence, or don't make the claim.*"

---

## 1. Architecture decisions — why I structured it this way (2:00)

*Say the decision, then the reason. Four decisions.*

**(a) A typed, checkpointed state machine — not a linear script, not a raw ReAct
blob.**
> "The core is a 25-node state machine over a checkpointed state object. I chose
> this so every run is resumable and every decision is *traced* — and so I could
> put an LLM inside the decision while keeping collection deterministic and
> safe. Open `outputs/runs/<RUN>/trace.jsonl`: it's the agent's full decision
> log — coverage assessed, actions proposed, action selected, with the
> alternatives it passed over."

**(b) The load-bearing split: models do bounded per-item work; code does
everything numeric.**
> "The single most important decision. The models classify one page, judge one
> claim, pick one next action, draft one play. Python does every comparative
> number — ownership shares, proof gaps, rankings, every chart. Why: it makes a
> re-render deterministic, verdicts can't drift between reads, and a hallucinated
> number physically can't reach the brief."

**(c) A real agentic loop — the LLM makes the research decision.**
> `grep '"decision_by"' outputs/runs/<RUN>/trace.jsonl`
> "The next action isn't a hardcoded score. A reasoning model gets the coverage
> state, the scored candidates, and what just succeeded or came back empty, and
> picks the next action with a written rationale — `decision_by: model`. It can
> only choose from the deterministically-generated candidates, so it can never
> invent an action, and if the model is unavailable it falls back to the
> deterministic scorer — `decision_by: heuristic`. Point it at two competitors
> and the research paths genuinely diverge — I verified that live on Deel,
> Gusto, and Workday."

**(d) The honesty layer is part of the architecture, not a disclaimer.**
> "Three things are enforced in code: a grounding gate drops any claim without
> verbatim evidence; every quote is containment-checked against the stored page;
> and comparisons are corpus-size-normalized so a 12-page niche site reads
> fairly against a 116-page corpus. The application is the accuracy gate, not
> the model. And Rippling runs through the *same* pipeline as an isolated mirror,
> so every 'them vs us' number uses symmetric methodology."

*(Optional: point at `docs/SYSTEMS_GUIDE.md` §2 for the loop diagram.)*

---

## 2. Tool & model choices — what I used and why (1:30)

**Model routing** (`config/model_routes.yaml`):
> "Two tiers. High-volume, bounded, schema-forced classification runs on
> **Haiku** — fast and cheap, and it's doing containment-checked extraction, not
> reasoning. Everything that needs judgment runs on the **Sonnet** reasoning
> tier: the in-loop planner, the temporal judge, claim judging, opportunity
> generation and critique, and the chat. I put reasoning where the task needs
> it and volume where it doesn't."

**Tools** (each is one `BaseTool` contract that turns every failure into a typed
result — that's the graceful-degradation story):
> "**Exa** for retrieval only — neural search, domain-scoped discovery,
> date-windowed prior sampling, per-post LinkedIn extraction, and the Similarweb
> traffic partner. Every Exa result re-enters my own classification and
> verbatim-verification, so a bad result can't become a fact. **Wayback** gives
> real archive capture dates for the temporal analysis. **SerpApi** pulls real
> Google ad creatives from the Transparency Center — the assignment lists ad
> libraries first, so that had to be real, not synthetic. **Gemini with Google
> Search grounding** gives live SERP intelligence for keyword targeting."

**Own the gaps honestly:**
> "Meta needs a developer token I didn't set up, so Meta ads are a *typed honest
> skip*, not fake data. And image/text ad creatives are rendered images, so I
> mark their copy 'not machine-readable' rather than OCR-guess. Google shows no
> spend or impressions for commercial ads, so I claim none."

---

## 3. How the eval system works (1:30)

*The key message: layered, and honest about which layers are objective.*

> "The eval is layered, and I'm explicit about which layers are real."

**Objective and final:**
> "Schema and excerpt validity, and the grounding gate — a material claim with
> no verbatim evidence, or a dangling evidence id, *fails the build*. These
> verify the pipeline's own invariants and they're non-negotiable."
> `uv run competitive-agent eval --suite all`

**Provisional — and labeled as such:**
> "Classification quality is measured by inter-model agreement: an independent
> Sonnet labeler versus the production Haiku classifier, on a held-out split. I
> label it *provisional, pending human adjudication* — I don't ship an accuracy
> number I haven't earned. And when I found a real measurement bug — a
> segment-vocabulary mismatch between the labeler and the taxonomy — I logged it
> in `docs/eval_harness_issues.md` rather than quietly fixing it."

**The frozen-surface discipline:**
> "The whole thing was hardened by an independent red-team loop over a *frozen*
> test surface — new tests only, never weakening an existing one — so the grade
> can only move when the product actually improves. That log is
> `docs/improvement_log.md`."
> `uv run competitive-agent eval-benchmark --package-run <RUN>`

---

## 4. Live demo — point it at a competitor (2:30)

*Use the finished Deel run (or run one live if Anthropic budget allows).*

**The agentic loop is real (30s):**
> `grep '"decision_by"' trace.jsonl` → *"model decisions with written
> rationales."* `grep tool_failed` / `grep source_skipped` → *"failures are typed
> results, the loop reacts to them."* `grep stop_selected` → *"it stops for an
> explainable reason."*

**The output — the deliverable (45s):**
> Open the brief (or the Overview tab). *"Start with the bottom line — composed
> only from verified numbers. Then the required deliverable section: the four
> questions answered from the evidence — their messaging themes, how they
> position their products, what changed recently, and the gaps and plays for
> Rippling — every row cited to a source artifact."* Point at the **download
> bar**: the markdown brief and the JSON package — the two required outputs.

**Real evidence (30s):**
> The ad creatives — *"real Google Transparency creatives with their permalink,
> not synthetic."* The LinkedIn tab — *"employee posts individually classified
> for theme and stance."*

**Conversational (45s):**
> Ask the chat a grounded question → cited answer. Ask it to *chart* something →
> *"the chat builds a graph on demand: the model picks what to chart, Python
> computes every number, so a fabricated chart can't appear."* Ask something the
> data can't answer → *"it doesn't bluff — it offers a scoped deeper-research
> pass on this same run."* Show the **✦ Ask AI** button on any section opening
> the context panel.

---

## Close (20s)

> "Everything you saw is reproducible from a stored run, runs locally, and works
> for any competitor at any size. Where I have gaps — Meta ads, image-ad OCR,
> human-adjudicated accuracy — they're disclosed in the product, not hidden.
> That's the bar I held the whole way: show the evidence, or don't make the
> claim."

---

## Q&A prep — see `docs/loom_talking_points.md`

The likely follow-ups and crisp answers: *how do I know it's not hallucinated*
(grounding gate + verbatim containment + deterministic recompute) · *model vs
code* (model: per-item + next-action; code: everything numeric) · *tiny
competitor* (corpus-size normalization) · *why Meta is empty* (no token — honest
skip) · *is the eval real* (objective layers final, classification provisional) ·
*what breaks it* (JS-only sites, ad-image OCR, thin prior windows).

## Data sources referenced (all public)
First-party website (sitemap + pages + pricing) · Wayback Machine (dated
snapshots) · Exa (search, discovery, LinkedIn extraction, Similarweb traffic) ·
SerpApi Google Ads Transparency Center (real ad creatives) · Meta Ad Library
(when a token is set) · Gemini + Google Search grounding (live SERP intel) ·
G2/Capterra/TrustRadius reviews · public news/press. No logins, no paywalls, no
non-public data.
