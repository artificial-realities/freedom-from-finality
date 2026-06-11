# Plan 01 — Native structured outputs

> **Status:** executed 2026-06-11. Self-contained.
>
> **Consumes:** `docs/EVOLUTION.md` §1.2 item 6 / §A.3 B5 (JSON-mode fragility).
>
> **Estimated work:** ~half day including live validation.

---

## 1. Goal

Replace prompt-and-pray JSON with OpenAI **native structured outputs** so malformed
JSON becomes impossible at the API level. Today every agent free-writes JSON guided
only by prose schema blocks in its prompt, and `src/util/json_sanitizer.py` runs a
3-stage repair pipeline (direct parse → extract → repair → skip turn). A parse
failure silently drops the turn's update — the B5 failure class.

This is a **no-regret** change: whatever the future ~3-role redesign looks like
(`docs/NORTH_STAR.md`), its agents will emit Pydantic schemas, and this
infrastructure transfers 1:1.

## 2. Validated facts (2026-06-11, against the live tree)

- `openai 2.33.0` and `langfuse 4.6.1` installed. The `langfuse.openai` drop-in
  wraps `chat.completions.create`, so `response_format` passes through untouched.
- `Beat`, `Shot`, `Commentary` convert cleanly via the SDK's strict transform
  (`openai.lib._pydantic.to_strict_json_schema`). `Shot.duration_seconds`'s
  `ge=3, le=15` becomes `minimum`/`maximum`, which the API now supports in strict
  mode.
- **`MemoryUpdate` is NOT strict-compatible**: `WorldStateDelta.characters:
  dict[str, dict]` emits `additionalProperties: {…}`, which strict mode rejects at
  request time (the SDK transform does not raise — the 400 comes from the API).
  Remodeling the free-form dict is out of scope: Spock's memory layer is replaced
  wholesale by the memory-swap plan, so don't invest in its schema now.
- `AnthropicBackend` (the judge) does **not** subclass `LLMBackend` — extending the
  base class cannot break it. The judge keeps using `parse_structured_response`
  (Anthropic path is untouched by this plan).

## 3. Design

**Strict where possible, schema-guided elsewhere.**

| Agent | Schema | Mode |
|---|---|---|
| Tolkien | `Beat` | `strict: true` — schema-enforced |
| Spielberg | `Shot` | `strict: true` |
| Attenborough | `Commentary` | `strict: true` |
| Spock | `MemoryUpdate` | `strict: false` — guaranteed-valid JSON, schema advisory |

Even non-strict `json_schema` mode guarantees syntactically valid JSON — which alone
removes the B5 failure class. Schema *conformance* for Spock continues to rely on
Pydantic validation in the agent, exactly as today.

Changes:

1. **`src/llm/base.py`** — add `generate_structured(messages, schema, …, strict=True)`
   as a **concrete** method that falls back to plain `generate()` (ignoring the
   schema), so any backend without native support keeps working.
2. **`src/llm/openai_backend.py`** — override `generate_structured`: build
   `response_format={"type": "json_schema", "json_schema": {name, strict, schema}}`
   using `to_strict_json_schema(model)` when strict, `model.model_json_schema()`
   when not. Same error handling as `generate`.
3. **`src/agents/_common.py`** — `call_llm_structured` gains `schema: type[BaseModel]`
   (required) and `strict: bool = True`; calls `generate_structured`. The
   `parse_structured_response` call **stays** as belt-and-braces (handles the
   truncated-by-`max_tokens` edge where `finish_reason == "length"` yields
   incomplete JSON even under structured outputs).
4. **Agents** — pass their schema class (already imported for validation). Spock
   passes `strict=False` with a comment naming the free-form-dict reason.
5. **`requirements.txt`** — `openai>=1.40.0` (first version with the strict
   transform; installed 2.33.0 satisfies it).

**Not changed:** prompt schema blocks (they carry field *semantics*, which strict
mode doesn't replace; the "Return JSON only" lines become harmless redundancy),
the judge path, `json_sanitizer.py` itself (now a fallback layer + judge
infrastructure, no longer load-bearing for agents).

Note on the private helper: `openai.lib._pydantic.to_strict_json_schema` is the
same transform the SDK's own `chat.completions.parse()` uses internally. We call
`create()` rather than `parse()` because `parse()` forces `strict=True`, which
`MemoryUpdate` can't satisfy — one uniform code path beats two.

## 4. Validation

1. `python -c "from main import main"` — imports clean.
2. Offline: strict transform succeeds for Beat/Shot/Commentary; request payload
   builds for all four.
3. Live smoke: `python main.py play --config configs/mas.yaml --scenario
   data/demo_scenario.json` (5 turns ≈ 20 gpt-4.1 calls). Confirm all four agents
   produce parsed output every turn (no "structured parse failed" / validation
   warnings in the log), and Spock's non-strict call doesn't 400.

## 5. Docs to update

- `docs/ARCHITECTURE.md` — "All four agents emit structured output …" paragraph and
  the **JSON Sanitizer** section: sanitizer is now a fallback layer, not
  load-bearing; mention strict vs non-strict split.
- `plans/README.md` — tick this item off the roadmap.
