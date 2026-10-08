.PHONY: ci-infra infra-up infra-down install install-dev run test lint typecheck security smoke ci scenarios greenfield brownfield ambiguous ambiguous-llm

install:
	pip install -r requirements.txt
install-dev:
	pip install -r requirements-dev.txt
run:
	uvicorn urlshort.main:app_factory --factory --reload --port 8000 --no-access-log --log-config config/uvicorn-logging.json
test:
	pytest
lint:
	ruff check urlshort sdlc tests
typecheck:
	mypy
security:
	bandit -q -r urlshort
	bandit -q -r sdlc -ll
	pip-audit -r requirements.txt --progress-spinner off
smoke:
	./scripts/smoke_test.sh http://localhost:8000
# Same gates as CI (except the Docker integration job)
ci: lint typecheck security
	pytest --cov=urlshort --cov=sdlc --cov-branch
	coverage report --include="urlshort/*" --omit="urlshort/adapters/*" --fail-under=100   # adapters: make ci-infra
	coverage report --include="sdlc/*" --fail-under=100

# SDLC scenarios (need tags v0.9.0 and v0.10.0); reports land in runs/<name>/run-report.md
greenfield brownfield ambiguous:
	python -m sdlc.cli run scenarios/$@.json --approvals scenarios/approvals/$@.json --run-dir runs/$@
scenarios: greenfield brownfield ambiguous ambiguous-llm

ambiguous-llm:
	python -m sdlc.cli run scenarios/ambiguous-llm.json --approvals scenarios/approvals/ambiguous.json --run-dir runs/ambiguous-llm

# Postgres + Redis adapters against real servers (docker compose); CI runs the same in its infrastructure job
INFRA_ENV = URLSHORT_TEST_DATABASE_URL=postgresql://urlshort:urlshort@localhost:5432/urlshort \
            URLSHORT_TEST_REDIS_URL=redis://localhost:6379/0
infra-up:
	docker compose up -d --wait
infra-down:
	docker compose down
ci-infra: infra-up
	$(INFRA_ENV) pytest tests/infra --cov=urlshort --cov-branch -o addopts="" -q
	coverage report --include="urlshort/adapters/*" --fail-under=100
