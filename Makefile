PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
RUFF ?= .venv/bin/ruff
NODE ?= node

.PHONY: check test test-ai2s lint check-clean
check: lint test test-ai2s
	$(PYTHON) scripts/check_repository.py

lint:
	$(RUFF) check scripts/agentic scripts/check_repository.py tests/agentic
	$(RUFF) format --check scripts/agentic scripts/check_repository.py tests/agentic

test:
	$(PYTHON) -B scripts/agentic/check.py

test-ai2s:
	$(NODE) -e 'if (process.versions.node.split(".")[0] !== "24") { console.error("AI2S tests require Node.js 24"); process.exit(1); }'
	$(NODE) --test examples/ai2s-skills-intake/tests/*.test.cjs

check-clean:
	git diff --check
	$(PYTHON) -c 'import subprocess; s=subprocess.check_output(["git","status","--porcelain"], text=True); print(s, end=""); raise SystemExit(bool(s))'
