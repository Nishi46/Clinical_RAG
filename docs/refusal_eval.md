# Refusal evaluation (S4-09)

**Refusal accuracy: 73.3%** (n=30 adversarial questions genuinely unanswerable from the corpus -- correctly says `NOT_ANSWERABLE`).

**Over-refusal rate: 6.7%** (n=30 already-answerable T1/T2 questions -- incorrectly refuses when the answer is actually retrievable).

A hedge ("I'm not sure, but possibly X") is never counted as a refusal on either side -- only an exact `NOT_ANSWERABLE` token counts, the same check `generate_answer` itself uses.
