
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.utils import timezone

class Command(BaseCommand):
    help = "Create a JSON backup of the database."

    def handle(self, *args, **options):
        filename = f"backup_{timezone.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w', encoding='utf-8') as f:
            call_command('dumpdata', '--natural-foreign', '--natural-primary', '--exclude', 'contenttypes', '--indent', '2', stdout=f)
        self.stdout.write(self.style.SUCCESS(f"Backup written to {filename}"))
