


test: venv  ## 🎯 Unit tests for Bluesky SECoP Integration
	. .venv/bin/activate && pytest -v . --ignore=frappy








venv: .venv/touchfile

.venv/touchfile: pyproject.toml 
	python3 -m venv .venv
	. .venv/bin/activate; pip install --upgrade pip; pip install -e .[dev]
	export FRAPPY_DIR=$(pwd)
	echo FRAPPY_DIR
	touch .venv/touchfile



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