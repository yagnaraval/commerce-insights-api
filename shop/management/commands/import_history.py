import json

from django.core.management.base import BaseCommand, CommandError

from shop.services.importer import import_csv


class Command(BaseCommand):
    help = "Import historical orders from a CSV file. Prints one JSON summary line."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to order_history.csv")

    def handle(self, *args, **options):
        try:
            summary = import_csv(options["path"])
        except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
            raise CommandError(f"cannot read {options['path']}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise CommandError(f"{options['path']} is not valid UTF-8: {exc}") from exc
        self.stdout.write(json.dumps(summary))
