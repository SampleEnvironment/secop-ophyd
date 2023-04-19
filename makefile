SRC_DIR := src








test: venv  ## 🎯 Unit tests for Flask app
	. $(SRC_DIR)/.venv/bin/activate \
	python3 frappy/bin/frappy-server frappy/cfg/cryo_cfg.py cryo &
	&& pytest -v















venv: $(SRC_DIR)/.venv/touchfile

$(SRC_DIR)/.venv/touchfile: $(SRC_DIR)/requirements.txt
	python3 -m venv $(SRC_DIR)/.venv
	. $(SRC_DIR)/.venv/bin/activate; pip install -Ur $(SRC_DIR)/requirements.txt
	touch $(SRC_DIR)/.venv/touchfile
