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
