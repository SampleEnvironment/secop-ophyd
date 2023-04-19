








test: venv  ## 🎯 Unit tests for Flask app
	. .venv/bin/activate \	
	&& pytest -v















venv: .venv/touchfile

.venv/touchfile: requirements.txt 
	python3 -m venv .venv
	. .venv/bin/activate
	ls -l
	pip install -Ur requirements.txt
	touch .venv/touchfile
