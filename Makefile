preflight:
	python -m vouch preflight

test:
	python -m pytest -m "not live" -q
