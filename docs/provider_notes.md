# Provider notes

Per §37.3 Rule 2: inspect current official provider docs before implementing an
adapter, isolate provider-specific code in the adapter, and record the exact
fields used. Verified 2026-07-11.

## Exa Search — `POST https://api.exa.ai/search`

Auth: `x-api-key` header (never logged; redacted via `security.redact_secrets`).
Called with a direct `httpx.AsyncClient` (20 s timeout, 2 retries on 5xx/timeout)
— **not** the public-URL fetch pipeline, since api.exa.ai is an authenticated
provider endpoint, not a page to fetch.

Request fields used:
- `query` (str)
- `numResults` (int)
- `contents: {"text": true}` — returns provider-extracted page text with each hit
- `startPublishedDate` (ISO date, optional) — publication-date lower bound
- `includeDomains` / `excludeDomains` (list[str], optional)

Response fields consumed per result:
- `url`, `title`, `text`, `publishedDate` (ISO-8601 → parsed; None if absent),
  `id`, `score`, `author`

Adapter behavior:
- missing API key → `ToolResult(status="unsupported")` ("provider not configured"),
  never a crash;
- HTTP 401/403 → `failed_terminal` (auth); 429 → `failed_retryable`;
- empty results → `empty` + a negative observation carrying the exact query;
- every artifact records the exact query and filters in `metadata` (provenance).

Limitation: Exa text is provider-extracted and may be partial; the pipeline
prefers first-party fetched pages over search snippets when both support the
same claim (§37.12), and never treats "no results" as "no activity".

Docs consulted: https://docs.exa.ai/reference/search (2026-07-11).

## Wayback Machine CDX — `http://web.archive.org/cdx/search/cdx`

Query: `url`, `output=json`, `from`/`to` (YYYYMMDD), `filter=statuscode:200`,
`collapse=timestamp:6` (≈ monthly). Snapshot markup fetched via
`https://web.archive.org/web/<timestamp>id_/<url>` (the `id_` infix returns raw
original markup without the replay toolbar). The **actual** CDX capture
timestamp is stored in `archive_capture_at` — never the requested window bound.
Archive absence is recorded as a coverage gap, never as page absence.

## Anthropic — Messages API (forced tool use)

Structured output via a single tool whose `input_schema` is the target Pydantic
model's JSON schema, with `tool_choice={"type":"tool","name":...}`. One repair
retry on validation failure; optional escalation to the stronger model. Model
ids are configuration (`config/model_routes.yaml`), logged per call, never
hardcoded in logic. Keys read from env only; never logged.

## Similarweb via Exa — `POST https://api.exa.ai/agent/runs`

Reached by attaching the **Similarweb provider to an Exa Agent run** (Exa
Connect) — NOT plain Exa search, and NOT the SharedHttp public-fetch pipeline.
Called with a direct `httpx.AsyncClient` (`x-api-key` header), like the Exa
search adapter. Request body:

```jsonc
{
  "query": "Using Similarweb, report web-analytics estimates for <domain>: …",
  "dataSources": [{ "provider": "similarweb" }],   // provider EXPLICITLY attached
  "outputSchema": { /* bounded JSON Schema, fields below only */ }
}
```

Bounded `outputSchema` fields requested (and nothing else):
`estimated_monthly_visits`, `observation_period`, `traffic_trend`
(`[{period, visits}]`), `channel_mix` (shares for
`direct/referral/social/organic_search/paid_search/display/mail`),
`top_countries` (`[{country, share}]`), `digital_competitors`
(`[{domain, affinity}]`), and `estimated_paid_keywords` (best-effort, "when
returned"). If the run is async, it is polled a bounded number of times
(`GET /agent/runs/{id}`) inside the boundary timeout — it never blocks.

Blueprint §39.7 shows the SDK kwarg `data_sources=[{"provider":"similarweb"}]`;
the REST body field is camelCase `dataSources`, consistent with the rest of the
Exa API (`numResults`, `includeDomains`).

Capability-check + labeling (§37.12, §39.7):
- keep ONLY fields the provider actually returned; **missing fields stay
  missing** — never synthesized; unrecognized channel keys are dropped;
- every metric is wrapped `{value, estimated: true, unit}` and the artifact
  `metadata` carries `{provider: "similarweb", observation_period,
  estimated: true, unit, retrieval_timestamp}` plus the validated `metrics`;
- `source_type = "similarweb"`, `collection_method = "exa_similarweb"`,
  provenance `url = https://www.similarweb.com/website/<domain>/`, and the exact
  Exa query + `dataSources` are recorded in `metadata`.

Adapter behavior (non-blocking, provider-dependent):
- missing `exa_api_key` → `unsupported` ("provider not configured"), no request;
- endpoint/provider absent (HTTP 404/405/501) → `unsupported` cleanly;
- HTTP 401/403 → `failed_terminal`; 429 / 5xx → `failed_retryable`; run that
  never completes within the poll budget → `failed_retryable`;
- empty/absent payload → `empty` + a negative observation; some-but-not-all core
  fields → `partial` with the missing fields disclosed. Every metric is an
  ESTIMATE; the report must not depend on keyword/spend estimates.

Docs consulted: https://exa.ai/docs/reference/agent-api/connect/similarweb and
https://exa.ai/docs/reference/agent-api/connect/overview (2026-07-11). The Agent
`POST /agent/runs` + `dataSources`/`outputSchema` shape is provider-dependent and
may evolve; the adapter degrades to `unsupported` if it becomes unavailable.
