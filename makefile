ifeq ($(OS),Windows_NT)
    activate = ./venv/Scripts/activate
    
else
    UNAME_S := $(shell uname -s)
    ifeq ($(UNAME_S),Linux)
        activate = .venv/bin/activate
    endif
endif

cryo: frappy/cfg/cryo_cfg.py
	python frappy/bin/frappy-server frappy/cfg/cryo_cfg.py  > cryo_log.txt  2>&1 & jobs -p > pids.txt

	



test: venv cryo  ## 🎯 Unit tests for Bluesky SECoP Integration
	sleep 1

	. $(activate) && pytest -v test/test.py
	kill $$(cat pids.txt)







venv: .venv/touchfile

.venv/touchfile: requirements.txt 
	python -m venv .venv
	. $(activate); pip install -Ur requirements.txt
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
