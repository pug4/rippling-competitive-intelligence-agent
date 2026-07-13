"""Cross-cutting synthesis helpers that raise strategic fidelity (feedback P0).

These turn a website-heavy corpus into *honest* company-level conclusions:
authority-weighted positioning (not raw frequency), corpus-skew detection,
source distribution, per-dimension coverage detail, and proof distributions.
Shared by the comparison engine, the graph nodes, and the report renderer so
the same rules apply everywhere.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .schemas.artifact import RawArtifact
from .schemas.classification import MarketingClassification
from .schemas.opportunity import CoverageDetail, ProofDistribution

# Marketing-surface authority (feedback #1): homepage/platform/product pages are
# company-level; blog/docs/support are product- or topic-level. A detailed
# tax-form claim on a blog page must not become the "dominant company message".
_SURFACE_AUTHORITY: dict[str, float] = {
    "home": 1.0,
    "platform": 0.95,
    "product": 0.85,
    "pricing": 0.7,
    "comparison": 0.6,
    "customers": 0.55,
    "segment": 0.5,
    "press": 0.4,
    "news": 0.4,
    "changelog": 0.3,
    "about": 0.3,
    "other": 0.15,
}
_SOURCE_TYPE_AUTHORITY: dict[str, float] = {
    "webpage": 0.8,  # refined by page_category below
    "sitemap": 0.0,
    "wayback": 0.5,
    "news": 0.4,
    "comparison": 0.6,
    "exa_web": 0.35,
}

# Proof types that count as genuinely strong.
_STRONG_PROOF = {
    "quantified_customer_outcome",
    "independent_validation",
    "product_demonstration",
    "named_customer_story",
    "certification_or_compliance_record",
}
_MODERATE_PROOF = {"customer_quotation", "customer_logo"}

# Classifier fallback labels that must never surface as real personas/CEPs
# (red-team: "not_observed" rendered as a buying trigger, "unclassified_signals:
# ..." rendered as a persona).
_PLACEHOLDER_RX = re.compile(
    r"^(not[_ ]observed|\(?unspecified\)?|unknown|none|n/?a|unclassified.*)$", re.IGNORECASE
)


def is_placeholder_label(label: str | None) -> bool:
    """True for classifier fallback values that aren't real observations."""
    return bool(_PLACEHOLDER_RX.match((label or "").strip()))


def _norm_label(label: str) -> str:
    """Normalize a free-form label to snake_case so 'growing remote and
    international teams' and 'growing_remote_and_international_teams' merge."""
    return re.sub(r"\s+", "_", label.strip().lower())


def assign_window(artifact: RawArtifact, time_windows: list[Any]) -> str:
    """THE single window-membership rule (red-team: two divergent predicates
    let the same artifact be 'prior' in change detection and 'current' in the
    baseline). Undated live content is current (retrieved now); dated content
    belongs to the window its date falls in; dates outside both windows are
    'outside' — excluded from both, never silently dumped into current."""
    comparison = next(
        (w for w in time_windows if getattr(w, "purpose", None) == "comparison"), None
    )
    current = next((w for w in time_windows if getattr(w, "purpose", None) == "current"), None)
    dated = artifact.archive_capture_at or artifact.published_at
    if dated is None:
        return "current"
    if comparison and comparison.start_at <= dated <= comparison.end_at:
        return "prior"
    if current and dated >= current.start_at:
        return "current"
    return "outside"


def is_junk_ads_artifact(url: str, metadata: dict[str, Any] | None, advertiser_domain: str) -> bool:
    """True for ads-transparency DISCOVERY results that are not advertiser pages
    (red-team: FAQ page, blank-query pages, other advertisers' pages all stamped
    advertiser='Deel'). Gated on the discovery-pointer flag so fixture/live ad
    CREATIVES are never touched."""
    if not (metadata or {}).get("is_discovery_pointer"):
        return False
    from html import unescape
    from urllib.parse import parse_qs, urlsplit

    # Discovery URLs arrive HTML-entity-encoded (&amp;) from page scrapes —
    # parse_qs would see the key 'amp;domain' and the rule would fail open.
    parts = urlsplit(unescape(url or ""))
    if not re.search(r"/advertiser/AR\d+", parts.path):
        return True  # FAQ, blank landing/query pages — not an advertiser surface
    domain_param = (parse_qs(parts.query).get("domain") or [""])[0].lower().strip()
    if domain_param and advertiser_domain:
        own = advertiser_domain.lower().strip()
        # Label-boundary match: 'deel.com' matches 'deel.com' and 'app.deel.com'
        # but NOT 'wheeldeel.com' (substring containment failed open).
        if domain_param != own and not domain_param.endswith("." + own):
            return True  # explicitly about a different advertiser's domain
    return False


def _url_path(url: str) -> str:
    from urllib.parse import urlsplit

    # Strip a wayback prefix if present, then take the path.
    if "web.archive.org" in url and "/https://" in url:
        url = url.split("id_/", 1)[-1] if "id_/" in url else url
    return (urlsplit(url).path or "/").rstrip("/") or "/"


def _path_surface(path: str) -> str | None:
    """Map a URL path to a marketing surface, independent of source_type — so an
    Exa-discovered or archived homepage is still recognized as the homepage
    (reviewer R2: an exa_web homepage must not be flattened to 0.35)."""
    if path in ("", "/"):
        return "home"
    first = path.strip("/").split("/")[0].lower()
    # Platform pages appear under many slugs: /platform, /hr-platform,
    # /workforce-platform, /product-platform, etc. Match the suffix so a
    # captured platform page is never mislabeled "not captured" (and gets the
    # high platform authority, not a 0.15 default).
    if first == "platform" or first.endswith("-platform") or first.endswith("platform"):
        return "platform"
    if first in ("pricing", "plans"):
        return "pricing"
    if first in ("products", "product", "solutions", "solution"):
        return "product"
    if first in ("customers", "customer", "case-studies"):
        return "customers"
    return None


def artifact_authority(artifact: RawArtifact) -> float:
    """0..1 weight for how company-representative a surface is (feedback #1).

    Authority is driven by the URL PATH first (a homepage is a homepage whether
    fetched first-party, discovered via Exa, or archived), then by page_category,
    then by source type. This stops a niche product page out-weighting the real
    homepage just because the homepage arrived as an ``exa_web`` result (R2).
    """
    if artifact.source_type == "sitemap":
        return 0.0
    surface = _path_surface(_url_path(artifact.final_url or artifact.url))
    category = str(artifact.metadata.get("page_category", "")).lower()
    if surface and surface in _SURFACE_AUTHORITY:
        base = _SURFACE_AUTHORITY[surface]
        # A first-party fetch of the surface is fully authoritative; an archived
        # or Exa-discovered copy of the same surface is slightly discounted.
        if artifact.source_type == "webpage":
            return base
        if artifact.source_type in ("wayback", "exa_web"):
            return max(base * 0.85, _SOURCE_TYPE_AUTHORITY.get(artifact.source_type, 0.2))
    if artifact.source_type == "webpage" and category in _SURFACE_AUTHORITY:
        return _SURFACE_AUTHORITY[category]
    return _SOURCE_TYPE_AUTHORITY.get(artifact.source_type, 0.2)


def _artifacts_by_id(artifacts: list[RawArtifact]) -> dict[str, RawArtifact]:
    return {a.artifact_id: a for a in artifacts}


def dominant_message(
    classifications: list[MarketingClassification], artifacts: list[RawArtifact]
) -> dict[str, Any]:
    """Authority-weighted dominant theme + whether it qualifies as *company*
    level (feedback #1): repeated across ≥2 source classes AND present on a
    top-level marketing surface. Otherwise it's only 'most prominent in the
    collected corpus', not the company's dominant message.
    """
    by_id = _artifacts_by_id(artifacts)
    theme_weight: dict[str, float] = {}
    theme_surfaces: dict[str, set[str]] = {}
    theme_source_classes: dict[str, set[str]] = {}
    theme_label: dict[str, str] = {}
    theme_best: dict[str, float] = {}
    for c in classifications:
        theme = c.primary_theme or c.primary_message
        if not theme:
            continue
        art = by_id.get(c.artifact_id)
        authority = artifact_authority(art) if art else 0.2
        sal = c.message_salience if c.message_salience is not None else 0.5
        weight = authority * (0.5 + sal)
        theme_weight[theme] = theme_weight.get(theme, 0.0) + weight
        # Recognize the surface by URL PATH first (a discovered/archived homepage
        # is still the homepage), then page_category, then source type.
        surface = None
        if art:
            surface = (
                _path_surface(_url_path(art.final_url or art.url))
                or str(art.metadata.get("page_category"))
                or None
                or art.source_type
            )
        # Filter the literal string "None" (from str(metadata.get(...))) so JSON
        # consumers never see a bogus surface (audit polish).
        if surface in ("None", "none", ""):
            surface = None
        theme_surfaces.setdefault(theme, set()).add(surface or "unknown")
        theme_source_classes.setdefault(theme, set()).add(art.source_type if art else "unknown")
        # The human-readable LABEL comes from the page that best REPRESENTS the
        # theme: authority AND salience together. A salience floor stops a
        # very-low-salience *niche* page (e.g. a mobility page at 0.24) from
        # supplying the label (R1) — but a company surface (home/platform) is
        # representative by definition, so the floor is waived there and it gets
        # a bonus. Without this, the label fell through to an arbitrary off-theme
        # message (QA finding #5: theme=consolidation, label was a pricing line,
        # because the real consolidation messages on the home/platform pages had
        # low classifier salience and were excluded).
        is_company_surface = surface in ("home", "platform")
        floor = 0.0 if is_company_surface else 0.35
        if sal >= floor:
            surface_bonus = 0.35 if is_company_surface else 0.0
            label_score = authority * (0.4 + sal) + surface_bonus
            if label_score > theme_best.get(theme, -1):
                theme_best[theme] = label_score
                theme_label[theme] = c.primary_message or theme

    if not theme_weight:
        return {"theme": None, "is_company_level": False, "label": None, "reason": "no themes"}

    top = max(theme_weight, key=lambda t: theme_weight[t])
    # Fallback label if no page cleared the salience floor for the top theme.
    if top not in theme_label:
        for c in classifications:
            if (c.primary_theme or c.primary_message) == top and c.primary_message:
                theme_label[top] = c.primary_message
                break
    # Company-level requires a genuine home/platform surface — NOT a product page
    # alone (R2): 6 product pages must not certify a company-level claim.
    company_surfaces = {"home", "platform"}
    on_company_surface = bool(theme_surfaces[top] & company_surfaces)
    multi_source = len(theme_source_classes[top]) >= 2
    is_company_level = on_company_surface and multi_source
    return {
        "theme": top,
        "label": theme_label.get(top, top),
        "is_company_level": is_company_level,
        "surfaces": sorted(theme_surfaces[top]),
        "source_classes": sorted(theme_source_classes[top]),
        "reason": (
            "repeated across ≥2 source classes and present on a top-level surface"
            if is_company_level
            else "most prominent in the collected corpus, but not confirmed on a top-level "
            "surface across multiple source classes"
        ),
    }


def corpus_skew(artifacts: list[RawArtifact]) -> list[str]:
    """Warn when the corpus is too concentrated to support company-level claims
    (feedback #36)."""
    warnings: list[str] = []
    collectible = [a for a in artifacts if a.source_type != "sitemap"]
    n = len(collectible)
    if n == 0:
        return ["corpus is empty"]
    by_source = Counter(a.source_type for a in collectible)
    if by_source.most_common(1)[0][1] / n > 0.6:
        s, cnt = by_source.most_common(1)[0]
        warnings.append(f"{cnt}/{n} artifacts are '{s}' — one source class dominates the corpus")
    low_authority = sum(1 for a in collectible if artifact_authority(a) <= 0.35)
    if low_authority / n > 0.5:
        warnings.append(
            f"{low_authority}/{n} artifacts are low-authority (blog/docs/search/exa) — "
            "top-level marketing surfaces are under-represented"
        )
    # Warn specifically when the two most company-representative surfaces are
    # missing (R2): a corpus of product pages alone cannot certify company-level
    # positioning. Recognize the surface by URL path, not only page_category.
    surfaces = {_path_surface(_url_path(a.final_url or a.url)) for a in collectible}
    if "home" not in surfaces:
        warnings.append(
            "no homepage was captured as a first-party page — company-level positioning is "
            "inferred from secondary surfaces and should be treated as provisional"
        )
    elif "platform" not in surfaces:
        warnings.append(
            "no platform page was captured — the platform/consolidation narrative is inferred "
            "from product and secondary pages"
        )
    return warnings


def source_distribution(artifacts: list[RawArtifact]) -> dict[str, int]:
    """Counts by a readable source label for the source-distribution table
    (feedback #8)."""
    labels: dict[str, int] = {}
    for a in artifacts:
        cat = str(a.metadata.get("page_category", "")).lower()
        if a.source_type == "webpage" and cat:
            label = {"home": "homepage/platform", "platform": "homepage/platform"}.get(
                cat, f"{cat} pages"
            )
        elif a.source_type == "sitemap":
            label = "site map"
        elif a.source_type == "wayback":
            label = "wayback snapshots"
        elif a.source_type in ("news",):
            label = "news/launches"
        elif a.source_type == "comparison":
            label = "comparison pages"
        else:
            label = a.source_type
        labels[label] = labels.get(label, 0) + 1
    return labels


def proof_distribution(proof_type_lists: list[list[str]]) -> ProofDistribution:
    """Aggregate per-page proof-type lists into an honest distribution
    (feedback #16): counts per type + an overall assessment that a single
    strong page cannot inflate."""
    counts: Counter[str] = Counter()
    n_pages = len(proof_type_lists)
    for types in proof_type_lists:
        for t in set(types):
            counts[t] += 1
    strong_pages = sum(1 for types in proof_type_lists if set(types) & _STRONG_PROOF)
    moderate_pages = sum(
        1
        for types in proof_type_lists
        if not (set(types) & _STRONG_PROOF) and set(types) & _MODERATE_PROOF
    )
    # Overall assessment weights how MANY pages carry strong proof, not whether
    # any single one does.
    strong_share = strong_pages / n_pages if n_pages else 0.0
    if strong_share >= 0.6:
        overall = "strong"
    elif strong_share >= 0.3 or (strong_pages and moderate_pages):
        overall = "weak-to-moderate"
    elif strong_pages or moderate_pages:
        overall = "weak-to-moderate"
    elif counts:
        overall = "weak"
    else:
        overall = "none"
    return ProofDistribution(
        counts=dict(counts),
        n_pages=n_pages,
        quantified_outcomes=counts.get("quantified_customer_outcome", 0),
        independent_validations=counts.get("independent_validation", 0),
        product_demonstrations=counts.get("product_demonstration", 0),
        named_customer_stories=counts.get("named_customer_story", 0),
        feature_assertions=counts.get("feature_assertion", 0),
        logos_only=counts.get("customer_logo", 0),
        overall_assessment=overall,
    )


_PRODUCT_ALIAS_RX = re.compile(r"^(.*?)\s*\((.*?)\)\s*$")


def _canon_product_key(name: str, company_name: str | None) -> str:
    """One key per product regardless of alias spelling (accuracy fix: the
    listing showed 'EOR (Employer of Record)' and 'Employer of Record (EOR)'
    as two products, splitting one product's counts across two rows). The key
    is order-insensitive over the name/parenthetical pair, with the company's
    own brand prefix stripped ('Deel Payroll' == 'Payroll')."""
    n = name.strip()
    if company_name:
        brand = company_name.split()[0].lower()
        if n.lower().startswith(brand + " "):
            n = n[len(brand) + 1 :]
    m = _PRODUCT_ALIAS_RX.match(n)
    if m:
        parts = sorted([m.group(1).strip().lower(), m.group(2).strip().lower()])
        return "|".join(parts)
    return n.lower()


def product_positioning(
    classifications: list[MarketingClassification], company_name: str | None = None
) -> list[dict[str, Any]]:
    """Aggregate observed product positioning by product (feedback #18): which
    themes, personas, and proof each named product carries in public pages.
    Deterministic over the classified corpus — no new extraction. Alias
    spellings of the same product merge into one row (first-seen display name)."""
    products: dict[str, dict[str, Any]] = {}
    for c in classifications:
        for p in c.products or []:
            raw = p.strip()
            if not raw:
                continue
            key = _canon_product_key(raw, company_name)
            slot = products.setdefault(
                key,
                {
                    "product": raw,
                    "themes": Counter(),
                    "personas": Counter(),
                    "proof_types": Counter(),
                    "ceps": Counter(),
                    "pages": 0,
                },
            )
            slot["pages"] += 1
            if c.primary_theme:
                slot["themes"][c.primary_theme] += 1
            for x in c.personas or []:
                if not is_placeholder_label(x):
                    slot["personas"][x] += 1
            for x in c.proof_types or []:
                slot["proof_types"][x] += 1
            for x in c.category_entry_points or []:
                if not is_placeholder_label(x):
                    slot["ceps"][_norm_label(x)] += 1
    # Second merge pass: alias spellings of ONE product must land in one row
    # ("EOR", "Employer of Record (EOR)", "HRIS", "HR (HRIS)", "HRIS (Human
    # Resources Information System)" are all the same product). Union keys
    # transitively over shared alias components — components are full alias
    # strings, never single words, so distinct products don't collapse.
    comp_sets: dict[str, set[str]] = {k: set(k.split("|")) for k in products}
    merged = True
    while merged:
        merged = False
        keys = sorted(comp_sets, key=lambda k: -products[k]["pages"])
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                if comp_sets[a] & comp_sets[b]:
                    target, src = products[a], products.pop(b)
                    target["pages"] += src["pages"]
                    for field in ("themes", "personas", "proof_types", "ceps"):
                        target[field].update(src[field])
                    comp_sets[a] |= comp_sets.pop(b)
                    merged = True
                    break
            if merged:
                break
    out = []
    for slot in sorted(products.values(), key=lambda s: -s["pages"]):
        if slot["pages"] < 1:
            continue
        out.append(
            {
                "product": slot["product"],
                "pages": slot["pages"],
                "themes": [t for t, _ in slot["themes"].most_common(3)],
                "personas": [t for t, _ in slot["personas"].most_common(3)],
                "proof_types": [t for t, _ in slot["proof_types"].most_common(3)],
                "category_entry_points": [t for t, _ in slot["ceps"].most_common(3)],
            }
        )
    return out[:12]


_OWNERSHIP_ORDER = {
    "competitor_advantage": 0,
    "contested": 1,
    "focal_owns": 2,
    "insufficient_sample": 3,
    "not_compared": 4,
    "neither": 5,
}


def category_entry_points(
    competitor_cls: list[MarketingClassification],
    focal_cls: list[MarketingClassification],
) -> list[dict[str, Any]]:
    """CEP ownership map: which buying triggers the competitor owns, the focal
    company owns, both contest, or neither addresses (feedback #22).

    Ownership is SHARE-NORMALIZED (red-team + niche-competitor requirement): a
    raw 79-vs-16 page count is meaningless when corpora differ in size, so each
    side's count is divided by its number of classified artifacts and the
    verdict comes from the share ratio. Thresholds: contested needs both sides
    >=2 pages and <2x share ratio (below 2x is crawl-composition noise); an
    ownership verdict needs >=2x share ratio (or a true zero on one side) AND
    >=3 pages on the dominant side (matches the corpus-wide thin<3 convention);
    anything thinner is disclosed as insufficient_sample, never asserted."""
    # No focal corpus (snapshot mode / focal mirror run itself): ownership
    # CANNOT be judged — a missing corpus is not a measured zero (verifier:
    # snapshot runs published "competitor_advantage" verdicts against a focal
    # side that was never collected).
    has_focal = bool(focal_cls)
    nc, nf = max(1, len(competitor_cls)), max(1, len(focal_cls))
    comp: Counter[str] = Counter()
    focal: Counter[str] = Counter()
    comp_examples: dict[str, list[str]] = {}
    focal_examples: dict[str, list[str]] = {}
    for c in competitor_cls:
        for cep in c.category_entry_points or []:
            if is_placeholder_label(cep):
                continue
            key = _norm_label(cep)
            comp[key] += 1
            comp_examples.setdefault(key, []).append(c.artifact_id)
    for c in focal_cls:
        for cep in c.category_entry_points or []:
            if is_placeholder_label(cep):
                continue
            key = _norm_label(cep)
            focal[key] += 1
            focal_examples.setdefault(key, []).append(c.artifact_id)
    rows: list[dict[str, Any]] = []
    for cep in set(comp) | set(focal):
        cn, fn = comp.get(cep, 0), focal.get(cep, 0)
        share_c, share_f = cn / nc, fn / nf
        hi, lo = max(share_c, share_f), min(share_c, share_f)
        # Thresholds compare the UNROUNDED ratio (verifier: a true 1.998 ratio
        # rounded to 2.0 would skip contested); rounding is display-only.
        raw_ratio = (hi / lo) if lo > 0 else None
        ratio = round(raw_ratio, 2) if raw_ratio is not None else None
        dominant_count = cn if share_c >= share_f else fn
        if not has_focal:
            ownership = "not_compared"
            basis = "no focal corpus collected this run — ownership not comparable"
        elif cn == 0 and fn == 0:
            ownership = "neither"
            basis = "no pages on either side"
        elif cn >= 2 and fn >= 2 and raw_ratio is not None and raw_ratio < 2.0:
            ownership = "contested"
            basis = f"{ratio}x share ratio — within crawl-composition noise"
        elif (raw_ratio is None or raw_ratio >= 2.0) and dominant_count >= 3:
            ownership = "competitor_advantage" if share_c > share_f else "focal_owns"
            basis = (
                f"{ratio}x share ratio, dominant side {dominant_count} pages"
                if ratio is not None
                else f"one-sided: dominant side {dominant_count} pages, other side 0"
            )
        else:
            ownership = "insufficient_sample"
            basis = f"too few pages to call ({cn} vs {fn}) — disclosed, not asserted"
        rows.append(
            {
                "cep": cep,
                "competitor_pages": cn,
                # A missing focal corpus is None, never a measured 0.
                "focal_pages": fn if has_focal else None,
                "competitor_share": round(share_c, 4),
                "focal_share": round(share_f, 4) if has_focal else None,
                "share_ratio": ratio if has_focal else None,
                "share_delta": round(share_c - share_f, 4),
                "ownership": ownership,
                "ownership_basis": basis,
                "competitor_example_artifact_ids": comp_examples.get(cep, [])[:5],
                "focal_example_artifact_ids": focal_examples.get(cep, [])[:3],
            }
        )
    rows.sort(
        key=lambda r: (
            _OWNERSHIP_ORDER.get(str(r["ownership"]), 9),
            -abs(float(r["share_delta"])),
        )
    )
    return rows


# Source type -> channel bucket for the persona×channel×funnel matrix.
_CHANNEL_OF_SOURCE = {
    "webpage": "website",
    "sitemap": "website",
    "wayback": "website (historical)",
    # Search-discovered pages are still website content — labeling them "social"
    # overstated social coverage (audit).
    "exa_web": "website (search-discovered)",
    "news": "press",
    "comparison": "comparison pages",
    "reviews": "review sites",
    "jobs": "jobs",
    "events": "events",
    "ooh": "ooh",
    "google_ads": "paid search",
    "meta_ads": "meta/instagram",
    "linkedin_ads": "paid linkedin",
    "similarweb": "traffic (estimated)",
}


def persona_channel_funnel(
    classifications: list[MarketingClassification],
    artifact_meta: dict[str, str],
) -> dict[str, Any]:
    """Persona × channel coverage matrix from the observed corpus (feedback #21).
    Cells are observed-page counts; empty cells are 'not observed', NOT proof of
    absence. ``artifact_meta`` maps artifact_id -> source_type."""
    matrix: dict[str, Counter] = {}
    personas_seen: set[str] = set()
    channels_seen: set[str] = set()
    for c in classifications:
        channel = _CHANNEL_OF_SOURCE.get(artifact_meta.get(c.artifact_id, ""), "other")
        channels_seen.add(channel)
        # Placeholder personas ("not_observed", "(unspecified)", raw
        # "unclassified_signals: ..." leaks) are classifier fallbacks, not
        # buyers — pages without a real persona contribute channels only.
        for persona in c.personas or []:
            if is_placeholder_label(persona):
                continue
            personas_seen.add(persona)
            matrix.setdefault(persona, Counter())[channel] += 1
    return {
        "personas": sorted(personas_seen),
        "channels": sorted(channels_seen),
        "cells": {p: dict(counts) for p, counts in matrix.items()},
    }


def commercial_motion(classifications: list[MarketingClassification]) -> dict[str, Any]:
    """Deterministic commercial-motion read from observed CTAs, pricing
    disclosure, and segment signals (feedback #20). No fabricated economics —
    this is a public-signal inference, never CAC/conversion/spend.
    """
    cta_counts: Counter[str] = Counter()
    pricing_levels: Counter[str] = Counter()
    segment_counts: Counter[str] = Counter()
    motion_signals: Counter[str] = Counter()
    for c in classifications:
        if c.cta:
            cta_counts[_normalize_cta(c.cta)] += 1
        if c.pricing_disclosure_level and c.pricing_disclosure_level != "unknown":
            pricing_levels[c.pricing_disclosure_level] += 1
        for s in c.segments or []:
            segment_counts[s] += 1
        for sig in c.commercial_motion_signals or []:
            motion_signals[sig.lower()] += 1

    total_cta = sum(cta_counts.values())
    dominant_ctas = (
        {k: round(v / total_cta, 2) for k, v in cta_counts.most_common(5)} if total_cta else {}
    )
    # Pricing disclosure is a BEST-EVIDENCE property, not a modal one: most pages
    # aren't pricing pages, so the mode says "hidden" even when the actual
    # pricing page publicly shows starting prices (audit: factual error — Deel's
    # /pricing shows "$14/worker/mo" yet the aggregate read "hidden"). Report the
    # MOST-DISCLOSING level with >=2 observations (noise guard against a single
    # stray classification); fall back to the most-disclosing single, then mode.
    _DISCLOSURE_OPENNESS = [
        "fully_public",
        "calculator",
        "starting_price_only",
        "mixed_by_product",
        "partially_public",
        "sales_gated",
        "hidden",
    ]
    pricing = next(
        (lvl for lvl in _DISCLOSURE_OPENNESS if pricing_levels.get(lvl, 0) >= 2),
        next(
            (lvl for lvl in _DISCLOSURE_OPENNESS if pricing_levels.get(lvl)),
            pricing_levels.most_common(1)[0][0] if pricing_levels else "unknown",
        ),
    )

    # Infer primary motion from the CTA mix + pricing gating (public signals).
    demo = cta_counts.get("book_demo", 0) + cta_counts.get("contact_sales", 0)
    free = cta_counts.get("start_free", 0) + cta_counts.get("sign_up", 0)
    if total_cta == 0:
        motion = "unclear"
    elif demo > free and pricing in ("sales_gated", "hidden", "starting_price_only"):
        motion = "sales_led" if free == 0 else "hybrid_sales_led"
    elif free > demo:
        motion = "product_led" if pricing in ("fully_public", "calculator") else "hybrid_plg"
    else:
        motion = "hybrid"

    return {
        "primary_motion": motion,
        "pricing_disclosure": pricing,
        "pricing_disclosure_mix": dict(pricing_levels),
        "dominant_ctas": dominant_ctas,
        "segment_focus": {k: v for k, v in segment_counts.most_common(4)},
        "signals": [s for s, _ in motion_signals.most_common(6)],
        "confidence": "medium" if total_cta >= 4 else "low",
        "basis": f"{total_cta} observed CTAs, {sum(pricing_levels.values())} pricing signals",
    }


_CTA_NORMAL = [
    ("book_demo", ("book a demo", "get a demo", "request a demo", "schedule a demo", "see a demo")),
    (
        "contact_sales",
        ("contact sales", "talk to sales", "talk to an expert", "get a quote", "request pricing"),
    ),
    ("start_free", ("start free", "get started free", "try free", "free trial", "start for free")),
    ("sign_up", ("sign up", "get started", "create account")),
    ("learn_more", ("learn more", "explore", "read more")),
]


def _normalize_cta(cta: str) -> str:
    low = cta.lower()
    for norm, variants in _CTA_NORMAL:
        if any(v in low for v in variants):
            return norm
    return "other"


def coverage_details(
    state: Any,
    artifacts: list[RawArtifact],
    classifications: list[MarketingClassification],
    coverage: dict[str, str] | None = None,
) -> list[CoverageDetail]:
    """Per-dimension coverage with the evidence behind each rating (feedback #7).
    Includes not_attempted dimensions — absences are findings, never hidden
    (red-team: 11 untouched dims were invisible to the brief AND to the
    largest-uncertainty line). ``coverage`` overrides state.coverage so the
    report can pass its recomputed honest levels."""
    from . import coverage as cov

    cov_map: dict[str, Any] = coverage if coverage is not None else state.coverage
    by_id = _artifacts_by_id(artifacts)
    # Map dimension -> source types that feed it.
    details: list[CoverageDetail] = []
    # Group artifacts by the source classes present.
    all_source_classes = sorted({a.source_type for a in artifacts if a.source_type != "sitemap"})
    windows_requested = len(state.time_windows)
    windows_present = len(
        {
            "current" if (a.archive_capture_at is None) else "historical"
            for a in artifacts
            if a.source_type != "sitemap"
        }
    )
    for dim in cov.COVERAGE_DIMENSIONS:
        level = cov_map.get(dim, "not_attempted")
        if level == "not_attempted":
            details.append(
                CoverageDetail(
                    dimension=dim,
                    level="not_attempted",
                    artifact_count=0,
                    source_classes=[],
                    requested_periods=windows_requested,
                    represented_periods=windows_present,
                    missing_sources=[],
                    reason="not attempted this run — absence of collection, not absence of activity",
                )
            )
            continue
        feeders = cov.DIMENSION_SOURCES.get(dim, [])
        contributing = [a for a in artifacts if a.source_type in feeders] if feeders else []
        # Consistent basis: with feeders, count+reason both describe the
        # contributing artifacts; without feeders the dimension is DERIVED from
        # classifications, and count+reason must say the same thing (verifier:
        # 10 rows shipped artifact_count=0 next to a "126 artifacts" reason).
        if feeders and contributing:
            src_classes = sorted({a.source_type for a in contributing})
            n_basis = len(contributing)
            reason = f"{n_basis} artifacts across {', '.join(src_classes)} sources"
        elif dim in cov.CLASSIFICATION_DERIVED_DIMENSIONS:
            src_classes = all_source_classes
            n_basis = len(classifications)
            reason = (
                f"derived from {n_basis} classified artifacts "
                f"(corpus-wide across {len(all_source_classes)} source classes)"
            )
        else:
            src_classes = all_source_classes
            n_basis = len(artifacts)
            reason = f"{n_basis} artifacts across {', '.join(src_classes) or 'mixed'} sources"
        missing = []
        for optional in ("paid_media", "public_social", "reviews"):
            if optional not in src_classes:
                missing.append(optional)
        details.append(
            CoverageDetail(
                dimension=dim,
                level=level,
                artifact_count=n_basis,
                source_classes=src_classes,
                requested_periods=windows_requested,
                represented_periods=windows_present,
                missing_sources=missing
                if dim in ("paid_media", "competitive_stance", "product_positioning")
                else [],
                reason=reason,
            )
        )
    del by_id
    return details


# ---------------------------------------------------------------------------
# Product-vertical analysis (per-offering view; Rippling competes across many
# categories, so analysis must not flatten them)
# ---------------------------------------------------------------------------


def _vertical_matchers(taxonomy: dict[str, Any]) -> dict[str, list[re.Pattern[str]]]:
    """Compile the config keyword map. Single tokens get word boundaries so
    short keywords ("eor", "pto") can't false-match inside other words;
    multi-word phrases match as substrings."""
    out: dict[str, list[re.Pattern[str]]] = {}
    for vertical, keywords in (taxonomy.get("product_verticals") or {}).items():
        pats: list[re.Pattern[str]] = []
        for kw in keywords or []:
            kw = str(kw).lower().strip()
            if not kw:
                continue
            if " " in kw:
                pats.append(re.compile(re.escape(kw)))
            else:
                pats.append(re.compile(rf"\b{re.escape(kw)}\b"))
        if pats:
            out[str(vertical)] = pats
    return out


def verticals_for_classification(
    c: MarketingClassification,
    artifact: RawArtifact | None,
    matchers: dict[str, list[re.Pattern[str]]],
) -> list[str]:
    """Deterministic vertical tags for one classified artifact. Precision rules
    (audit: ~6% single-keyword false positives, compliance as a catch-all):
    - a match in PRODUCTS or the URL PATH tags directly (high-specificity);
    - a match only in message/theme TEXT needs >=2 distinct keyword hits;
    - 'compliance_legal' additionally requires a legal/regulatory co-occurrence
      so the bare word 'compliance' (used everywhere) can't tag alone.
    Retroactive — needs no reclassification."""
    strong_parts: list[str] = list(c.products or [])
    if artifact is not None:
        strong_parts.append(_url_path(artifact.final_url or artifact.url))
    text_parts: list[str] = list(c.supporting_themes or [])
    if c.primary_theme:
        text_parts.append(c.primary_theme)
    if c.primary_message:
        text_parts.append(c.primary_message)
    text_parts.extend(c.secondary_messages or [])
    if artifact is not None and artifact.title:
        text_parts.append(artifact.title)

    def _norm(parts: list[str]) -> str:
        return " ".join(str(p) for p in parts).lower().replace("_", " ").replace("-", " ")

    strong_hay = _norm(strong_parts)
    text_hay = _norm(text_parts)
    _LEGAL_CTX = re.compile(r"\b(legal|regulat|gdpr|soc ?2|audit|law|statut)")

    hits: list[str] = []
    for v, pats in matchers.items():
        strong_hits = sum(1 for p in pats if p.search(strong_hay))
        text_hits = sum(1 for p in pats if p.search(text_hay))
        matched = strong_hits >= 1 or text_hits >= 2
        if matched and v == "compliance_legal" and strong_hits == 0:
            # generic 'compliance' language alone isn't the legal/compliance
            # product vertical — require legal/regulatory context.
            matched = bool(_LEGAL_CTX.search(text_hay))
        if matched:
            hits.append(v)
    return hits


def product_vertical_analysis(
    classifications: list[MarketingClassification],
    artifacts: list[RawArtifact],
    taxonomy: dict[str, Any],
) -> dict[str, Any]:
    """Per-vertical positioning view: for each product vertical the competitor
    touches — how many pages/posts, the themes and stances used there, personas
    addressed, and example sources. Keyword-derived (disclosed), deterministic."""
    matchers = _vertical_matchers(taxonomy)
    if not matchers:
        return {"verticals": [], "by_artifact": {}, "method": "no product_verticals in taxonomy"}
    by_id = _artifacts_by_id(artifacts)
    per: dict[str, dict[str, Any]] = {}
    by_artifact: dict[str, list[str]] = {}
    for c in classifications:
        art = by_id.get(c.artifact_id)
        hits = verticals_for_classification(c, art, matchers)
        if hits:
            by_artifact[c.artifact_id] = hits
        for v in hits:
            slot = per.setdefault(
                v,
                {
                    "vertical": v,
                    "n_artifacts": 0,
                    "n_linkedin_posts": 0,
                    "themes": Counter(),
                    "stances": Counter(),
                    "personas": Counter(),
                    "example_urls": [],
                    "sample_message": None,
                },
            )
            slot["n_artifacts"] += 1
            if art is not None and art.source_type == "linkedin_post":
                slot["n_linkedin_posts"] += 1
            if c.primary_theme:
                slot["themes"][c.primary_theme] += 1
            if c.competitive_stance:
                slot["stances"][c.competitive_stance] += 1
            for p in c.personas or []:
                if not is_placeholder_label(p):
                    slot["personas"][p] += 1
            if art is not None and len(slot["example_urls"]) < 3:
                u = art.final_url or art.url
                if u and u not in slot["example_urls"]:
                    slot["example_urls"].append(u)
            if slot["sample_message"] is None and c.primary_message:
                slot["sample_message"] = c.primary_message[:160]

    verticals = []
    for v, s in sorted(per.items(), key=lambda kv: -kv[1]["n_artifacts"]):
        verticals.append(
            {
                "vertical": v,
                "n_artifacts": s["n_artifacts"],
                "n_linkedin_posts": s["n_linkedin_posts"],
                "top_themes": [t for t, _ in s["themes"].most_common(3)],
                # Full per-vertical theme counts — powers the vertical x theme
                # heatmap (per-topic graphs for product marketing).
                "theme_counts": dict(s["themes"].most_common(8)),
                "stance_mix": dict(s["stances"]),
                "personas": [p for p, _ in s["personas"].most_common(3)],
                "example_urls": s["example_urls"],
                "sample_message": s["sample_message"],
            }
        )
    return {
        "verticals": verticals,
        "by_artifact": by_artifact,
        "method": "deterministic keyword mapping over products/themes/messages/url "
        "(config taxonomy.product_verticals); not a model judgment",
    }


def message_channel_alignment(
    classifications: list[MarketingClassification], artifacts: list[RawArtifact]
) -> dict[str, Any]:
    """Two deterministic DEEP insights from channel-vs-channel theme comparison:

    1. PAID vs ORGANIC: themes in ad creatives vs website pages — what the
       competitor PAYS to amplify reveals investment priorities; paid-only
       themes are pushes the site doesn't yet reflect (or landing mismatch).
    2. EMPLOYEE ADVOCACY: themes in employee LinkedIn posts vs the website —
       high alignment = disciplined narrative; employee-only themes are the
       unofficial story (often the roadmap/culture tell).

    Pure counting over already-classified artifacts; no model calls.
    """
    by_id = _artifacts_by_id(artifacts)
    website: Counter[str] = Counter()
    paid: Counter[str] = Counter()
    employee: Counter[str] = Counter()
    for c in classifications:
        theme = c.primary_theme
        if not theme:
            continue
        art = by_id.get(c.artifact_id)
        st = art.source_type if art else ""
        if st in ("webpage", "wayback"):
            website[theme] += 1
        elif st in ("google_ads", "meta_ads", "linkedin_ads"):
            paid[theme] += 1
        elif st == "linkedin_post":
            employee[theme] += 1

    def _overlap(a: Counter[str], b: Counter[str]) -> float:
        if not a:
            return 0.0
        return round(sum(n for t, n in a.items() if t in b) / max(1, sum(a.values())), 2)

    return {
        "website_themes": dict(website.most_common(8)),
        "paid_themes": dict(paid.most_common(8)),
        "employee_themes": dict(employee.most_common(8)),
        "paid_only_themes": sorted(set(paid) - set(website)),
        "employee_only_themes": sorted(set(employee) - set(website)),
        "paid_organic_alignment": _overlap(paid, website),
        "employee_advocacy_alignment": _overlap(employee, website),
        "method": "theme distributions per channel from classified artifacts (deterministic)",
    }


def temporal_baseline(
    classifications: list[MarketingClassification],
    artifacts: list[RawArtifact],
    time_windows: list[Any],
) -> dict[str, Any]:
    """Prior-vs-current window THEME BASELINE (presentation fix: change events
    only show emergences, so a reader concluded the prior window was empty when
    it actually held 14 dated artifacts). Prior membership = archive capture or
    published date inside the comparison window; undated live content counts as
    current (it was retrieved now). Deterministic."""

    prior_w = next((w for w in time_windows if getattr(w, "purpose", None) == "comparison"), None)
    if prior_w is None:
        return {}
    ps, pe = prior_w.start_at, prior_w.end_at
    by_id = _artifacts_by_id(artifacts)
    # ONE counting rule shared with change-event reconciliation: a theme is
    # present on an artifact when it is the primary OR a supporting theme,
    # counted once per artifact (verifier: the baseline counted primary-only
    # while events counted incl. supporting, so the same brief called a theme
    # "emerged (current only)" that its own event line counted in the prior
    # window).
    prior_themes: Counter[str] = Counter()
    current_themes: Counter[str] = Counter()
    n_prior = n_current = n_outside = 0
    counted_ids: set[str] = set()
    seen_theme_artifact: set[tuple[str, str]] = set()
    for c in classifications:
        art = by_id.get(c.artifact_id)
        if art is None:
            continue
        window = assign_window(art, time_windows)
        if art.artifact_id not in counted_ids:
            counted_ids.add(art.artifact_id)
            if window == "prior":
                n_prior += 1
            elif window == "current":
                n_current += 1
            else:
                n_outside += 1
        if window == "outside":
            continue
        for theme in [c.primary_theme, *(c.supporting_themes or [])]:
            if not theme or (theme, c.artifact_id) in seen_theme_artifact:
                continue
            seen_theme_artifact.add((theme, c.artifact_id))
            (prior_themes if window == "prior" else current_themes)[theme] += 1
    stable = sorted(set(prior_themes) & set(current_themes))
    emerged = sorted(set(current_themes) - set(prior_themes))
    receded = sorted(set(prior_themes) - set(current_themes))

    # Export FULL counters — a top-10 cap here dropped a theme (count 1) that a
    # change event counted in the prior window, recreating the contradiction at
    # the display layer. Renderers cap for layout; the data stays complete.
    def _shares(themes: Counter[str], n: int) -> dict[str, float]:
        return {t: round(v / max(1, n), 4) for t, v in themes.most_common()}

    note = (
        "Prior membership = real archive-capture/published date inside the comparison "
        "window; undated live content is current. Theme counts = artifacts carrying the "
        "theme as primary OR supporting (the same rule the change events use). Windows "
        f"have different sample sizes ({n_prior} vs {n_current}) — compare shares, not "
        "counts; treat emergence/recession as signals."
    )
    if n_outside:
        note += f" {n_outside} dated artifact(s) fall outside both windows and are excluded."
    return {
        "prior_window": {
            "start": ps.date().isoformat(),
            "end": pe.date().isoformat(),
            "n_artifacts": n_prior,
            "themes": dict(prior_themes.most_common()),
            "themes_share": _shares(prior_themes, n_prior),
        },
        "current_window": {
            "n_artifacts": n_current,
            "themes": dict(current_themes.most_common()),
            "themes_share": _shares(current_themes, n_current),
        },
        "outside_windows": n_outside,
        "stable_themes": stable,
        "emerged_themes": emerged,
        "receded_themes": receded,
        "note": note,
    }


# ---------------------------------------------------------------------------
# EDA-derived insight graphics (marketing-ops): five cross-cutting joins that
# single-dimension charts miss. Every block is pure deterministic counting over
# already-validated classifications — the EDA pass found the structure, this
# code reproduces it for ANY competitor pair, with n's and honest omissions
# (no focal mirror -> focal side absent, never zero-filled).
# ---------------------------------------------------------------------------

_STRONG_PROOF_TYPES = tuple(_STRONG_PROOF)


def _brand_token(name: str) -> str:
    return (name or "").split()[0].lower() if name else ""


def _proof_rate(cls_list: list[Any], proof: str) -> tuple[int, int]:
    hits = sum(1 for c in cls_list if proof in (c.proof_types or []))
    return hits, len(cls_list)


def _cep_pages(cls_list: list[Any], cep_key: str) -> list[Any]:
    return [
        c
        for c in cls_list
        if any(_norm_label(x) == cep_key for x in (c.category_entry_points or []) if x)
    ]


def insight_graphics(
    competitor_cls: list[MarketingClassification],
    competitor_arts: list[RawArtifact],
    focal_cls: list[MarketingClassification],
    ceps: list[dict[str, Any]],
    similarweb: dict[str, Any],
    comp_verticals_by_artifact: dict[str, list[str]],
    focal_verticals_by_artifact: dict[str, list[str]],
    focal_cls_arts_source: dict[str, str],
    competitor_name: str,
    focal_name: str,
) -> dict[str, Any]:
    """Five EDA-verified insight graphics. Each block carries its own n's,
    caveats, and an ops-executable action caption."""
    out: dict[str, Any] = {}
    nc, nf = len(competitor_cls), len(focal_cls)
    has_focal = nf > 0
    src_of = {a.artifact_id: a.source_type for a in competitor_arts}

    def side(cls_list: list[Any]) -> dict[str, Any]:
        voice = [
            c
            for c in cls_list
            if c.primary_theme == "compliance" or "compliance" in (c.supporting_themes or [])
        ]
        cert_n, _ = _proof_rate(voice, "certification_or_compliance_record")
        quant_n, _ = _proof_rate(voice, "quantified_customer_outcome")
        return {
            "n_classified": len(cls_list),
            "voice_n": len(voice),
            "voice_share": round(len(voice) / max(1, len(cls_list)), 4),
            "cert_n": cert_n,
            "cert_rate": round(cert_n / max(1, len(voice)), 4),
            "quant_standin_n": quant_n,
            "quant_standin_rate": round(quant_n / max(1, len(voice)), 4),
        }

    # -- 1. Claim vs record: compliance voiced vs certification shown --------
    comp_side = side(competitor_cls)
    if comp_side["voice_n"]:
        hit_list: list[dict[str, Any]] = []
        guardrail: list[dict[str, Any]] = []
        if has_focal:
            for row in ceps:
                key = str(row.get("cep") or "")
                cp = _cep_pages(competitor_cls, key)
                fp = _cep_pages(focal_cls, key)
                if len(cp) < 3 and len(fp) < 3:
                    continue
                c_cert, _ = _proof_rate(cp, "certification_or_compliance_record")
                f_cert, _ = _proof_rate(fp, "certification_or_compliance_record")
                c_rate = c_cert / max(1, len(cp))
                f_rate = f_cert / max(1, len(fp))
                entry = {
                    "cep": key,
                    "ownership": row.get("ownership"),
                    "competitor": {"cert_n": c_cert, "n": len(cp), "rate": round(c_rate, 4)},
                    "focal": {"cert_n": f_cert, "n": len(fp), "rate": round(f_rate, 4)},
                }
                if f_rate - c_rate >= 0.05 and len(fp) >= 3:
                    hit_list.append(entry)
                elif row.get("ownership") == "competitor_advantage" and abs(f_rate - c_rate) < 0.05:
                    guardrail.append(entry)
            hit_list.sort(key=lambda e: -(e["focal"]["rate"] - e["competitor"]["rate"]))
        block: dict[str, Any] = {
            "board_column": "ATTACK",
            "title": (
                f"{competitor_name} claims compliance on {comp_side['voice_share']:.0%} of pages "
                f"but shows a compliance record on {comp_side['cert_rate']:.0%} — "
                "quantified outcomes stand in"
            ),
            "competitor": comp_side,
            "read_in_5s": (
                "The wider the gap between the two dots, the more compliance is voiced "
                "without a matching certification record."
            ),
            "method": (
                "voice = classified pages carrying the compliance theme (primary or supporting); "
                "record = those pages carrying certification_or_compliance_record proof"
            ),
        }
        if has_focal:
            block["focal"] = side(focal_cls)
            block["cep_hit_list"] = hit_list[:4]
            block["guardrail"] = guardrail[:3]
            block["action"] = (
                f"Buy audit/certification intent where {focal_name}'s record rate beats "
                f"{competitor_name}'s (hit list); do NOT spend where the rates match "
                "(guardrail rows) — creative: certification wall vs story-backed claims, "
                "ungated comparison LP, gate only the audit-prep checklist."
            )
        else:
            block["action"] = (
                "No focal mirror this run — voice-vs-record shown for the competitor only."
            )
        out["claim_vs_record"] = block

    # -- 2. Proof vs voice: quantified-outcome rate on the owned triggers ----
    if has_focal and ceps:
        rows = []
        eligible = sorted(
            (
                r
                for r in ceps
                if (r.get("competitor_pages") or 0) + (r.get("focal_pages") or 0) >= 20
            ),
            key=lambda r: -((r.get("competitor_pages") or 0) + (r.get("focal_pages") or 0)),
        )
        ranked = eligible[:5]
        # The DEFEND story hinges on the focal-owned trigger — always include
        # the biggest one even when it isn't top-5 by combined volume.
        if not any(r.get("ownership") == "focal_owns" for r in ranked):
            best_owned = next((r for r in eligible if r.get("ownership") == "focal_owns"), None)
            if best_owned is not None:
                ranked.append(best_owned)
        for row in ranked:
            key = str(row.get("cep") or "")
            cp, fp = _cep_pages(competitor_cls, key), _cep_pages(focal_cls, key)
            cq, _ = _proof_rate(cp, "quantified_customer_outcome")
            fq, _ = _proof_rate(fp, "quantified_customer_outcome")
            rows.append(
                {
                    "cep": key,
                    "ownership": row.get("ownership"),
                    "competitor": {
                        "quant_n": cq,
                        "n": len(cp),
                        "rate": round(cq / max(1, len(cp)), 4),
                    },
                    "focal": {"quant_n": fq, "n": len(fp), "rate": round(fq / max(1, len(fp)), 4)},
                }
            )
        cq_all, _ = _proof_rate(competitor_cls, "quantified_customer_outcome")
        fq_all, _ = _proof_rate(focal_cls, "quantified_customer_outcome")
        comp_names = sum(
            1
            for c in competitor_cls
            if any(_brand_token(focal_name) in str(x).lower() for x in (c.named_competitors or []))
        )
        focal_names = sum(
            1
            for c in focal_cls
            if any(
                _brand_token(competitor_name) in str(x).lower() for x in (c.named_competitors or [])
            )
        )
        if rows:
            out["proof_vs_voice"] = {
                "board_column": "DEFEND",
                "title": (
                    f"Ownership by voice, not by proof: {competitor_name} quantifies "
                    f"{cq_all / max(1, nc):.0%} of its corpus vs {focal_name}'s "
                    f"{fq_all / max(1, nf):.0%} — including on triggers {focal_name} owns"
                ),
                "rows": rows,
                "overall": {
                    "competitor": {
                        "quant_n": cq_all,
                        "n": nc,
                        "rate": round(cq_all / max(1, nc), 4),
                    },
                    "focal": {"quant_n": fq_all, "n": nf, "rate": round(fq_all / max(1, nf), 4)},
                },
                "naming": {
                    "competitor_names_focal": comp_names,
                    "focal_names_competitor": focal_names,
                },
                "read_in_5s": (
                    "Where the competitor dot sits right of yours on a trigger YOU own by page "
                    "count, your ownership is voice without proof — a flank, not a moat."
                ),
                "action": (
                    f"Quantified case-study sprint on {focal_name}-owned triggers where the "
                    f"competitor out-proves you; freeze paid spend on triggers where their "
                    "quantified rate is a fortress."
                ),
            }

    # -- 3. Funnel voids: decision-stage assets per vertical -----------------
    all_verts = sorted(
        {v for vs in comp_verticals_by_artifact.values() for v in vs}
        | {v for vs in focal_verticals_by_artifact.values() for v in vs}
    )
    if all_verts:
        comp_by_art = {c.artifact_id: c for c in competitor_cls}
        focal_by_art = {c.artifact_id: c for c in focal_cls}

        def stage_counts(by_art: dict[str, Any], vmap: dict[str, list[str]], vert: str):
            pages = [by_art[a] for a, vs in vmap.items() if vert in vs and a in by_art]
            ev = sum(1 for c in pages if "evaluation" in (c.funnel_stages or []))
            de = sum(1 for c in pages if "decision" in (c.funnel_stages or []))
            return {"n": len(pages), "evaluation_n": ev, "decision_n": de}

        frows: list[dict[str, Any]] = []
        for vert in all_verts:
            comp_s = stage_counts(comp_by_art, comp_verticals_by_artifact, vert)
            focal_s = (
                stage_counts(focal_by_art, focal_verticals_by_artifact, vert) if has_focal else None
            )
            if comp_s["n"] < 5 and (not focal_s or focal_s["n"] < 5):
                continue
            frows.append(
                {
                    "vertical": vert,
                    "competitor": comp_s,
                    "focal": focal_s,
                    "void": bool(
                        comp_s["decision_n"] == 0
                        and comp_s["n"] >= 5
                        and focal_s
                        and focal_s["decision_n"] > 0
                    ),
                }
            )
        frows.sort(key=lambda r: (not bool(r["void"]), -int(r["competitor"]["n"])))
        rows = frows
        voids = [str(r["vertical"]) for r in rows if r["void"]]
        comp_dec_total = sum(1 for c in competitor_cls if "decision" in (c.funnel_stages or []))
        dec_verticals = sorted(
            {
                v
                for c in competitor_cls
                if "decision" in (c.funnel_stages or [])
                for v in comp_verticals_by_artifact.get(c.artifact_id, [])
            }
        )
        if rows:
            out["funnel_voids"] = {
                "board_column": "INTERCEPT",
                "title": (
                    f"{competitor_name} closes only on home turf: its {comp_dec_total} "
                    f"decision assets sit on {', '.join(dec_verticals[:3]) or 'few verticals'} — "
                    f"zero in {', '.join(v.replace('_', ' ') for v in voids[:3]) or 'no void found'}"
                    + (f", where {focal_name} has them" if voids and has_focal else "")
                ),
                "rows": rows,
                "competitor_decision_total": comp_dec_total,
                "competitor_decision_verticals": dec_verticals,
                "read_in_5s": (
                    "Rows at 0% decision with deep evaluation content strand a researching "
                    "buyer — whoever has a decision asset there catches them."
                ),
                "action": (
                    "Ship comparison LPs on the void verticals and bid the competitor's "
                    "branded queries there — they have no decision asset to answer with; "
                    "ungated, quote/demo CTA."
                ),
            }

    # -- 4. Affinity defense: audience overlap vs comparison-page census -----
    aff = ((similarweb or {}).get("metrics") or {}).get("digital_competitors") or {}
    aff_rows = aff.get("value") if isinstance(aff, dict) else aff
    comparison_slugs: list[str] = []
    for a in competitor_arts:
        if a.source_type == "sitemap":
            for p in (a.metadata or {}).get("page_map") or []:
                if p.get("category") == "comparison":
                    slug = str(p.get("url", "")).rstrip("/").rsplit("/", 1)[-1].lower()
                    if slug and "vs" not in slug.split("-"):
                        comparison_slugs.append(slug)
    if isinstance(aff_rows, list) and aff_rows and comparison_slugs is not None:

        def defended(domain: str) -> bool:
            base = str(domain).split(".")[0].lower()
            for slug in comparison_slugs:
                s = slug.replace("-", "")
                if s and (s in base or base in s):
                    return True
            return False

        def mentions(domain: str) -> int:
            base = str(domain).split(".")[0].lower().removeprefix("use").removesuffix("hr")
            return sum(
                1
                for c in competitor_cls
                if any(base in str(x).lower().replace(" ", "") for x in (c.named_competitors or []))
            )

        rows = [
            {
                "domain": r.get("domain"),
                "affinity": round(float(r.get("affinity", 0)), 2),
                "defended": defended(str(r.get("domain", ""))),
                "mentions": mentions(str(r.get("domain", ""))),
            }
            for r in aff_rows[:8]
            if isinstance(r, dict)
        ]
        matched = {
            s
            for s in comparison_slugs
            if any(
                s.replace("-", "") in str(r.get("domain", "")).split(".")[0]
                or str(r.get("domain", "")).split(".")[0] in s.replace("-", "")
                for r in aff_rows[:20]
                if isinstance(r, dict)
            )
        }
        orphan_slugs = sorted(set(comparison_slugs) - matched)
        undefended_top = [r for r in rows if not r["defended"]]
        if rows:
            out["affinity_defense"] = {
                "board_column": "SEO/CONQUEST",
                "title": (
                    f"{competitor_name}'s highest-affinity rival"
                    + (
                        f" ({undefended_top[0]['domain']}) has no comparison page"
                        if undefended_top and undefended_top[0] is rows[0]
                        else "s include undefended flanks"
                    )
                    + (
                        f" — while {len(orphan_slugs)} vs-page(s) target domains outside its "
                        "audience's top affinities"
                        if orphan_slugs
                        else ""
                    )
                ),
                "rows": rows,
                "orphan_comparison_slugs": orphan_slugs,
                "n_comparison_pages": len(comparison_slugs),
                "read_in_5s": (
                    "Grey (undefended) bars at the top = the audience's real destinations with "
                    "no comparison page — those SERPs are open to whoever moves first."
                ),
                "action": (
                    "Publish named comparison LPs against the undefended high-affinity domains "
                    "and bid their 'vs' queries; affinity is an estimated overlap index, not "
                    "lost-deal share (labeled on-chart)."
                ),
            }

    # -- 5. Channel proof split: what the feed shows that the site doesn't ---
    li = [c for c in competitor_cls if src_of.get(c.artifact_id) == "linkedin_post"]
    web = [c for c in competitor_cls if src_of.get(c.artifact_id) == "webpage"]
    if li and web:

        def split_side(li_c: list[Any], web_c: list[Any]) -> dict[str, Any]:
            d_li, _ = _proof_rate(li_c, "product_demonstration")
            d_web, _ = _proof_rate(web_c, "product_demonstration")
            q_li, _ = _proof_rate(li_c, "quantified_customer_outcome")
            q_web, _ = _proof_rate(web_c, "quantified_customer_outcome")
            no_pub = sum(
                1 for c in web_c if c.pricing_disclosure_level in ("hidden", "sales_gated")
            )
            no_cta = sum(1 for c in web_c if not c.cta)
            return {
                "linkedin_n": len(li_c),
                "web_n": len(web_c),
                "demo_linkedin": d_li,
                "demo_web": d_web,
                "quant_linkedin": q_li,
                "quant_web": q_web,
                "no_public_pricing_web": no_pub,
                "no_cta_web": no_cta,
            }

        comp_split = split_side(li, web)
        fli = [c for c in focal_cls if focal_cls_arts_source.get(c.artifact_id) == "linkedin_post"]
        fweb = [c for c in focal_cls if focal_cls_arts_source.get(c.artifact_id) == "webpage"]
        out["channel_proof_split"] = {
            "board_column": "WHITESPACE",
            "title": (
                f"The only place {competitor_name} shows its product is LinkedIn "
                f"({comp_split['demo_linkedin']}/{comp_split['linkedin_n']} posts) — its website "
                f"demos on {comp_split['demo_web']}/{comp_split['web_n']} pages and hides pricing "
                f"on {comp_split['no_public_pricing_web'] / max(1, comp_split['web_n']):.0%}"
            ),
            "competitor": comp_split,
            "focal": split_side(fli, fweb) if (fli or fweb) and has_focal else None,
            "read_in_5s": (
                "Demo proof crashes from feed to indexed site while outcome stats do the "
                "reverse — nobody serves demo intent on an indexable page; that SERP is unowned."
            ),
            "action": (
                "Ship one ungated interactive product-tour LP + a public 'what pricing depends "
                "on' table; exact-match paid on the competitor's demo/pricing queries; validate "
                "query volume before committing budget."
            ),
        }
    return out
