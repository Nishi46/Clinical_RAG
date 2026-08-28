# T2 correctness rubric

Written before any hand-labeling or judge-scoring for S3-08's calibration study, per the same
rubric-first discipline `discrepancy_definition.md` establishes for S4-07 — score against a fixed,
written definition, not a case-by-case gut call.

Applies to a generated answer scored against a T2 question's hand-written `gold_answer_notes`
(the human labeler's supporting quote(s) from the source protocol/SAP, not `gold_answer`'s
short paraphrase — the notes are the authoritative reference).

## Labels

| Score | Meaning |
|---|---|
| **1 — Correct** | The answer states every fact the question asks for, accurately, with no material omission and no incorrect addition. For a multi-part question ("what happens for Grade 2, and for Grade 3-4?"), every part must be answered correctly to earn a 1. |
| **0.5 — Partially correct** | The answer gets the core concept or the majority of a multi-part question right, but: misses one sub-part of a compound question, omits a qualifying condition or threshold that materially changes the answer, or states the right general idea with one incorrect specific detail (e.g. right mechanism, wrong number). The reader would come away with a mostly-correct but incomplete or slightly-off picture. |
| **0 — Incorrect** | The answer contradicts the reference notes, states an unrelated or fabricated fact, or refuses (`NOT_ANSWERABLE` / equivalent hedge) when the supplied excerpts do in fact contain the answer. In this calibration run every question is fed its own real gold chunks as context, so the answer is always in principle answerable — a refusal here is always a 0, not a defensible abstention. |

## Boundary cases, decided in advance

- **Extra correct detail beyond what was asked**: does not lower the score. Only a materially
  *missing* or *wrong* piece counts against it.
- **Correct answer, different phrasing/units than the notes**: scores 1 — this rubric grades
  factual correctness against the notes, not lexical similarity to them (that's what
  `exact_match_score`'s normalization is for on T1; T2 is judged precisely because it needs this
  looser standard).
- **Citation brackets (`[1]`, `[2]`) present or absent**: not scored here — citation accuracy is
  `faithfulness_score`'s job, not correctness's.
- **Hedged-but-still-correct answer** ("it appears the timeframe is X, based on the excerpt"):
  scores on the substance, not the hedge — 1 if X is right and complete.

## Scope note

This rubric grades **correctness of content**, mirroring `judged_correctness`'s job. It is
deliberately silent on faithfulness/grounding (a separate, already-implemented atomic-claim check,
S3-07) and on citation quality — conflating those into one score would make disagreements harder to
diagnose.
