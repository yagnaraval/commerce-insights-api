PY ?= python

.PHONY: install setup import train run test all

install:
	$(PY) -m pip install -r requirements.txt

setup:  ## create schema + seed items (lamp, stool, cushion)
	$(PY) manage.py migrate

import:
	$(PY) manage.py import_history data/order_history.csv

train:
	$(PY) manage.py train_return_model

run:
	$(PY) manage.py runserver

test:
	$(PY) manage.py test

all: install setup import train test
