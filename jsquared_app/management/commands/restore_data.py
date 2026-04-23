
from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
    help = "Restore a JSON backup file."

    def add_arguments(self, parser):
        parser.add_argument('filepath')

    def handle(self, *args, **options):
        call_command('loaddata', options['filepath'])
        self.stdout.write(self.style.SUCCESS('Backup restored.'))
