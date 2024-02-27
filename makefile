
export WORK_DIR := ${PWD}
export PATH_VAR := ${PATH}

test: venv  ## 🎯 Unit tests for Bluesky SECoP Integration
	. .venv/bin/activate && pytest -v . --ignore=frappy
	


.env: 
	echo "WORK_DIR=${WORK_DIR}\nPATH_VAR=${PATH_VAR}" > .env


venv: .env .venv/touchfile

.venv/touchfile: pyproject.toml 
	python3 -m venv .venv
	. .venv/bin/activate; pip install --upgrade pip; pip install -e .[dev]
	touch .venv/touchfile


pretty: venv .env
	. .venv/bin/activate; black src tests docs; isort src tests docs; flake8 src tests docs; mypy src tests docs ; pre-commit run --all-files

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

