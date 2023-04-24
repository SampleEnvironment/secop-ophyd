


test: venv  ## 🎯 Unit tests for Bluesky SECoP Integration
	. .venv/bin/activate && pytest -v test/test.py








venv: .venv/touchfile

.venv/touchfile: requirements.txt 
	python3 -m venv .venv
	. .venv/bin/activate; pip install -Ur requirements.txt; pip install -Ue .
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