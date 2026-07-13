# How to describe the platform + anticipated GTM-engineering Q&A

Companion to `loom_script.md` (the shot list). This is WHAT to say about the
platform and the follow-ups to expect. Every number below is verified against
the flagship run (RUN-b256fab1c1dd) and the RemoFirst run (RUN-aa58e6d812da).

---

## The one-liner

> "It's a competitive-intelligence agent that turns any competitor's public
> marketing into an evidence-grounded battle plan for Rippling — every claim
> traceable to a source, every verdict normalized for corpus size, and every
> recommendation shipped with the experiment design to test it."

## The 60-second description (use this shape everywhere)

1. **What it does:** give it a competitor name or domain; it autonomously
   researches their public marketing — website, archived history, LinkedIn
   posts, news, ads-transparency, traffic estimates — classifies every page
   individually, mirrors the same analysis for Rippling, and produces one
   dashboard: current positioning, changes over time, where they're vulnerable,
   and ranked plays with kill rules.
2. **How it works (three layers):**
   - **An agentic research loop** — a typed state machine that assesses
     coverage, proposes scored actions, executes tools, and stops for an
     explainable reason. Every decision and the alternatives NOT chosen are in
     the trace.
   - **A deterministic analysis layer** — every verdict on screen (ownership,
     proof gaps, temporal changes, EDA insights) is computed by counting over
     validated classifications, not generated as prose. The LLMs classify and
     judge individual items; the application does the math.
   - **An honesty layer** — grounding gates (a claim with no verbatim evidence
     is dropped), outlier floors (an ATTACK verdict needs ≥5 pages or ≥15% of
     the corpus), share normalization (a 12-page niche site compares fairly
     against a 116-page corpus), and disclosed absence ("we looked and found
     nothing" is rendered, never hidden).
3. **Why it's different from "ask ChatGPT about Deel":** nothing on screen is
   model prose about the world. The model never asserts a fact the pipeline
   didn't collect; the app never renders a number it can't recompute; and you
   can hover ANY classification and see the competitor's actual verbatim
   wording with its source. Then it goes past analysis into operations: each
   play carries a metric, guardrails, staged proceed/stop gates, and a kill
   rule — it's a campaign board, not a book report.

## The architecture, in the order to say it

- **Collection (Exa for retrieval only):** first-party fetch with robots
  respected → sitemap-driven page prioritization; Exa neural search for
  discovery, date-windowed prior sampling, per-post LinkedIn extraction, and
  the Similarweb enrichment. Every Exa result is re-classified and
  verbatim-verified by our pipeline — Exa scrapes, Claude reasons.
- **Classification (Haiku, schema-forced):** every page/post gets themes,
  personas, funnel stages, proof types, buying triggers (CEPs), stance,
  pricing disclosure — with verbatim excerpts that are containment-checked
  against the page text (fabricated quotes get dropped at extraction).
- **Judgment (reasoning tier):** temporal judge (both-period evidence rule),
  claim judge (narrowing + banned-term guard: ROAS/CAC/spend can never ship),
  opportunity critics (genericness + superiority filters).
- **Synthesis (deterministic Python):** ownership shares, proof-debt joins,
  funnel voids, temporal reconciliation, the scorecard, the bottom line — all
  pure counting, unit-tested, reproducible from the stored run.
- **The focal mirror:** Rippling runs through the SAME pipeline in an isolated
  run, so every "they vs us" number uses symmetric methodology.

## Numbers you can say on camera (all verified)

- Flagship live run: 126 artifacts across 9 source classes, 123 classified,
  30 LinkedIn posts individually classified, 20/20 claims cited with 0 broken
  evidence references, 19 planning cycles, ~$7 total spend, budget-bounded stop.
- 257 automated tests; grounding/validity gates objective and final;
  classification accuracy human-adjudicated on a held-out set.
- RemoFirst run: 75 vs 116 classified pages — the share-normalization proof;
  the system flags its own #1 play as THIN (3 competitor pages) and says
  "verify before spending."

---

## Anticipated GTM-engineering follow-ups (with answers)

**Q1. "How do I know the numbers aren't hallucinated?"**
Three mechanisms, in order: (1) excerpts are containment-verified against the
fetched page at extraction — a quote that isn't in the page is dropped and
logged; (2) a grounding gate fails the build if any material claim lacks
evidence ids or any evidence id dangles (this run: 20/20, 0 broken); (3) every
chart number is computed by deterministic Python over stored classifications —
you can recompute any of them from data.json, and our own red-team agents did
exactly that, digit for digit.

**Q2. "What did the model decide vs what did code decide?"**
Models do bounded, per-item work: classify one page, judge one claim, draft one
opportunity. Code does everything comparative and numeric: shares, ownership,
gaps, temporal reconciliation, rankings. That split is why re-rendering a run
is deterministic and why verdicts can't drift between reads.

**Q3. "Why Exa and not Google/Bing/scraping myself?"**
Exa gives four retrieval primitives in one API: neural search with domain
scoping, published-date windows (for the prior-period sample), an agent that
extracts individual LinkedIn posts with real URLs, and the Similarweb data
partner. We deliberately use it ONLY for retrieval — every result re-enters
our own classification and verification pipeline, so a bad Exa result can't
become a fact.

**Q4. "What happens with a tiny competitor — doesn't a 10-page site break all
your ratios?"**
That's the corpus-normalization layer: every cross-company comparison divides
by that company's classified corpus, ownership verdicts need both a share
ratio AND a minimum page count, thin reads render as "insufficient sample"
instead of verdicts, and an asymmetry banner fires when corpora differ >3× or
either side is under 20 pages. The RemoFirst run (75 vs 116) is the live
proof.

**Q5. "How current is the data, and how would this stay fresh?"**
Each run is a timestamped snapshot; every artifact carries its retrieval or
archive-capture date. Freshness is a re-run — runs are cheap (~$7, ~30-60 min
live) and queue from the UI. Productionizing would schedule weekly runs per
competitor and diff against the prior run — the child-run machinery
(`deepen`/`challenge`/`retry` with difference reports) already exists.

**Q6. "What does a run cost and how does it scale?"**
~$5-7 of model spend + ~$2-4 Exa for a full comparative run (the focal mirror
is cached and reused across runs). Cost scales with pages classified, which is
capped by config. Scaling to 50 competitors is a queue and a database — the
portfolio command already runs several in isolated pipelines with a
cross-company leak check.

**Q7. "Why should I trust the temporal 'changes' — archives are spotty."**
The system agrees with you, structurally: a change needs evidence in BOTH
windows; emergence claims are reconciled against the final corpus (three
signals on the flagship were auto-relabeled from 'emerging' to 'expanding'
when the full corpus showed prior presence); every event carries a
coverage-asymmetry alternative explanation; and you can re-slice the windows
live to test whether a change survives moving the line.

**Q8. "The stop reason says the run hit a budget — is the analysis
incomplete?"**
It's budget-BOUNDED, and it says exactly what that means: which dimensions
were never attempted are listed as limitations, the coverage table shows the
basis per dimension, and the trust envelope on the Overview states corpus
sizes up front. Honest boundedness beats fake completeness — and the budget is
config.

**Q9. "Is scraping competitors' sites okay?"** 
Public marketing pages only; robots.txt respected on first-party fetches (the
Exa fallback is for HTTP failures, never a robots bypass); no logins, no
paywalls, no personal data beyond public posts by named employees; ad-library
data comes from Google's own transparency surface. Meta/LinkedIn ad libraries
have no public commercial API, so those are recorded as honest typed skips.

**Q10. "How do you know the classifications themselves are right?"**
Layered evals: schema/excerpt validity and grounding are objective, automated,
and final; classification quality is measured by inter-model agreement and
then human adjudication on a held-out set — the report ships the adjudicated
number, not a self-graded one.

**Q11. "What would you build next?"**
(1) Scheduled re-runs + run-over-run diffs as alerts ("Deel added decision
content in benefits"); (2) deeper SERP/keyword-volume joins so the ops plays
carry real search volumes; (3) Exa Monitors/Websets for standing watches; (4)
CRM joins (win/loss by competitor) so plays close the loop to revenue.

**Q12. "Why did you build the UI this way — six tabs?"**
Each tab answers one question a GTM person actually asks (start here / where
to win / what their people say / what changed / where's the demand / why trust
this), each opens with the question it answers, sections are numbered in
reading order, every finding carries a visible one-line justification, and the
messaging popup means no claim is more than one hover from its evidence.

**Q13. "Why don't you show search volumes or the keywords they bid on?"**
Because they're not publicly knowable, and we refuse to fake them. Ad
transparency libraries prove WHICH creatives a competitor is running — never
the keyword buy, the spend, or the auction (Meta discloses spend only for
political ads). Similarweb's keyword product isn't exposed through Exa's
provider (verified by live probe — it returns visits, rank, and audience-
affinity competitors, which we DO use). So the paid-search view drafts
keyword clusters as explicit hypotheses grounded in observed evidence — CEPs,
verbatim villain wording, live creatives — with validate-before-spend forced
on every cluster and legal review auto-flagged on brand conquesting. A GTM
engineer plugs the clusters into Keyword Planner; the system never pretends
to know the economics.

**Q14. "What happens if a run dies halfway — or I close the tab?"**
Nothing is lost. Every node transition checkpoints the full typed state to
SQLite, so the run list is rebuilt from the database, not server memory:
refresh the page mid-run and it's still there, streaming progress. Restart
the server and the orphaned run shows up as "interrupted" with everything it
collected intact — one click resumes it from the last checkpoint without
re-fetching or re-paying for anything, and a run that died between its stop
decision and the report write just replays the deterministic render.

**Q15. "What breaks it?"** (be honest, it lands well)
JS-only sites with no crawlable content degrade first-party coverage (Exa
fallback helps); LinkedIn depends on Exa credits (it's a toggle and the run
says so when it fails); tiny prior windows make temporal reads signals, not
facts — which is exactly why they're labeled low-confidence; and the taxonomy
is config, so a brand-new category needs a keyword-list update.
