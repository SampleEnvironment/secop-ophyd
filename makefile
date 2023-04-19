








test: venv  ## 🎯 Unit tests for Flask app
	. .venv/bin/activate \	
	&& pytest -v















venv: .venv/touchfile

.venv/touchfile: requirements.txt frappy/requirements.txt
	python3 -m venv .venv
	. .venv/bin/activate
	pip install -Ur requirements.txt
	pip install -Ur frappy/requirements.txt
	touch .venv/touchfile
