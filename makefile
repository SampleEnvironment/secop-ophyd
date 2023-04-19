








test: venv  ## 🎯 Unit tests for Flask app
	. .venv/bin/activate \
	&& python3 frappy/bin/frappy-server frappy/cfg/cryo_cfg.py cryo & \
	&& pytest -v















venv: .venv/touchfile

.venv/touchfile: requirements.txt
	python3 -m venv .venv
	. .venv/bin/activate; pip install -Ur requirements.txt
	touch .venv/touchfile
