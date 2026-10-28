PYTHON ?= python3

.PHONY: test check smoke dist

test:
	$(PYTHON) -m unittest discover -s tests -v

check:
	$(PYTHON) scripts/check_source.py
	node --check dashboard/app.js

smoke:
	$(PYTHON) scripts/smoke_test.py

dist:
	$(PYTHON) scripts/make_dist.py
