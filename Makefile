.PHONY: lint typecheck test test-db fmt corpus-report ingestion-report ablation

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
