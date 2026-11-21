#!/bin/sh
set -e

python manage.py migrate
python manage.py import_history data/order_history.csv
python manage.py train_return_model

exec "$@"
