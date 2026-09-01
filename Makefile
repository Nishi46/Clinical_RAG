.PHONY: lint typecheck test test-db fmt corpus-report ingestion-report ablation discrepancy-eval phrase-pairs normalization-eval discrepancy-detect t3-questions

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest -m "not integration and not db"

test-db:
	pytest -m db

fmt:
	ruff format .
	ruff check --fix .

corpus-report:
	python -m protocol_drift.reports.corpus_report

ingestion-report:
	python -m protocol_drift.reports.ingestion_report

ablation:
	python -m protocol_drift.eval.ablation

discrepancy-eval:
	python -m protocol_drift.eval.discrepancy_scorer

phrase-pairs:
	python scripts/build_phrase_pairs.py

normalization-eval:
	python scripts/run_normalization_eval.py

discrepancy-detect:
	python scripts/run_discrepancy_detector.py

t3-questions:
	python -m protocol_drift.eval.t3_questions
