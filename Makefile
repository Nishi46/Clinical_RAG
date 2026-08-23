.PHONY: lint typecheck test fmt corpus-report

lint:
	ruff check .

typecheck:
	mypy src

test:
	pytest -m "not integration"

fmt:
	ruff format .
	ruff check --fix .

corpus-report:
	python -m protocol_drift.reports.corpus_report
