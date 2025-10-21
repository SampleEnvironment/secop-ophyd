


test: venv  ## 🎯 Unit tests for Bluesky SECoP Integration
	source .venv/bin/activate && pytest -v .



venv: .venv/touchfile

.venv/touchfile: pyproject.toml
	uv venv --python 3.11
	uv sync --all-groups
	touch .venv/touchfile


pretty: venv
	pre-commit run --all-files

clean:  ## 🧹 Clean up project
	rm -rf .venv
	rm -rf tests/node_modules
	rm -rf tests/package*
	rm -rf test-results.xml
	rm -rf __pycache__
	rm -rf .pytest_cache
	rm -rf pids.txt
	rm -rf *_log.txt
	rm -rf *egg-info
	rm -rf build
	rm -rf dist
	rm -rf .env
