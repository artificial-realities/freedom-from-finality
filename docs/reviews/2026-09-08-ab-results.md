# A/B: Director engine vs legacy four-role engine (2026-09-08)

Same scripted 30-turn scenario (`data/ab_scenario.json`), same model (`gpt-4.1`), headless. Transcripts: `logs/director_ab_scenario_20260908_114659.md` and `logs/mas_ab_scenario_20260908_114701.md`. N=1, so read the structural columns, not the prose.

| Measure | Director (`configs/director.yaml`) | Legacy (`configs/mas.yaml`) |
|---|---|---|
| Wall time, 30 turns | 178 s | 265 s |
| LLM calls | 39 (30 Director + 3 re-asks + 6 Showrunner) | 105 (30 × Tolkien, Spielberg, Spock + 15 Attenborough) |
| Structured-output failures | 0 | 0 |
| Narrator lines | 9, gated by a 15 s silence floor | 15, every other turn |
| Prop ledger | 5 props tracked with status and holder on every turn | inventory `['']` on turns 4–8, empty on turns 10–24 while the cap, skateboard, harmonica and ball were all in play |
| Plan | 2-act arc, 4 scenes, 9 threads opened and paid off | none (one-sentence direction) |
| Memory | scene summaries of 65–109 words, act summary 129 words, plus structured state | rolling prose of 73–339 words rewritten every turn |
| t23 "Where did the harmonica go?" | found it where the ledger said it was | found it (prose memory carried it) |
| t26 "Do the thing with the hat again" | hat bit with the cap | hat bit with the cap |
| t29 "Show us the very first thing you did" | **deferred** with a stated reason, recorded as owed | **ignored** silently |
| t30 "Take a bow with everything" | gathered all four props plus the banana peel | bowed with the harmonica and a ball-and-cap tower; described "the skateboard and classic black hair" as props |

## What the run changed

- The three re-asks were all the model putting "at the_white_void" or "on_skateboard" in `location`. Locations now resolve leniently and an unknown one is a warning that drops the field, not a re-ask.
- Turn 29 showed the Director letting a fresh scene card outrank the viewer. The Director prompt now states that a request outranks the plan: absorb it in the segment whenever physically possible, defer only when it cannot happen yet, and deliver it next segment.
- Known limitation: one disposition per segment. A deferred request is resolved only on a later segment with no new input, so two requests in consecutive segments leave the first owed. Acceptable for now; visible in the "owed" list.

## Verdict

The Director engine is faster, makes a third of the calls, keeps a correct prop ledger where the legacy engine's was wrong for 22 of 30 turns, and has a plan that actually advances (four scenes in 30 segments versus one apple for 113 in the last live run). Prose-level memory probes were a tie at 30 turns, which matches the 2026-06 finding that 30 turns is inside the prose rewrite's comfort zone. The legacy engine can be deleted once one longer run (100+ segments) confirms the ledger and plan hold up.
