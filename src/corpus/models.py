"""Model IDs and prices shared by the pipeline scripts (not a CLI).

SONNET is the default for vision OCR, CJK translation, band/tile re-reads,
blocked-page recovery and translation. HAIKU is not a
default anywhere: on OCR and translation it changes printed taxon names, and it
left half of a German paper untranslated. Change them here, not in each script. Confirm a new ID against the API
first (GET /v1/models); don't guess.

Every per-page and per-chunk cache records the model that wrote it and only
reuses an entry written by the SAME model. Switching the default would then
make any rebuild of an existing key (a --pages repair, which rebuilds the
whole markdown from cache) re-OCR every page. Models in one CACHE_COMPATIBLE
group are treated as interchangeable for cache hits, so entries written by an
earlier model stay valid. Only new calls use the new model.
"""

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"

CACHE_COMPATIBLE = [
    {"claude-sonnet-5-5", "claude-sonnet-5"},
    {"claude-haiku-5-5", "claude-haiku-4-5-20251001", "claude-haiku-4-5"},
]

# USD per million tokens, from platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-10-07). cache_w is the 5-minute write. Haiku 5.5 is priced by
# prompt length: these are the rates for prompts up to 100,000 tokens, which
# every pipeline request is (a translation chunk or one page image). Over that,
# Haiku 5.5 costs 5x: $0.50 / $2.50.
PRICES = {
    "claude-sonnet-5-5":         {"in": 2.0, "out": 10.0, "cache_r": 0.10, "cache_w": 2.50},
    "claude-sonnet-5":           {"in": 2.0, "out": 10.0, "cache_r": 0.20, "cache_w": 2.50},
    "claude-sonnet-4-6":         {"in": 3.0, "out": 15.0, "cache_r": 0.30, "cache_w": 3.75},
    "claude-haiku-5-5":          {"in": 0.10, "out": 0.50, "cache_r": 0.01, "cache_w": 0.125},
    "claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0, "cache_r": 0.10, "cache_w": 1.25},
    "claude-haiku-4-5":          {"in": 1.0, "out": 5.0, "cache_r": 0.10, "cache_w": 1.25},
}


def cache_model_ok(cached_model: str | None, requested_model: str) -> bool:
    """True if a cache entry written by cached_model may serve requested_model."""
    if cached_model == requested_model:
        return True
    return any(cached_model in g and requested_model in g for g in CACHE_COMPATIBLE)


def request_extras(model: str) -> dict:
    """Extra messages.create() kwargs a model needs for verbatim OCR/translation.

    Haiku 5.5 thinks by default (adaptive, effort medium), and thinking tokens
    count toward max_tokens. Transcription and translation gain nothing from it,
    so turn it off. Sent via extra_body because the pinned SDK (anthropic
    0.46.0) predates the `thinking` parameter. Sonnet 5.5 rejects
    {"type": "disabled"} with a 400, so other models get nothing.
    """
    if model == HAIKU:
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}


def cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
         cache_read: int = 0, cache_write: int = 0) -> float:
    """USD for one call (or a sum of calls) at `model`'s prices.

    Unknown models are priced as SONNET."""
    p = PRICES.get(model, PRICES[SONNET])
    return (input_tokens * p["in"] + output_tokens * p["out"]
            + cache_read * p["cache_r"] + cache_write * p["cache_w"]) / 1_000_000
