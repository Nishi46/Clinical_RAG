.PHONY: lint typecheck test test-db fmt corpus-report

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
