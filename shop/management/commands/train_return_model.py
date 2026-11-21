import json

from django.core.management.base import BaseCommand, CommandError

from shop.ml.training import TrainingError, train


class Command(BaseCommand):
    help = "Train the return-risk model on imported history; writes models/return_model.joblib and metrics.json."

    def handle(self, *args, **options):
        try:
            metrics = train()
        except TrainingError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(metrics))
