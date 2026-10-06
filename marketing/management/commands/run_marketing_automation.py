import time
from django.core.management.base import BaseCommand
from marketing.services.automation import run_due


class Command(BaseCommand):
    help = 'Run due marketing automations. Use --loop for continuous polling.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **opts):
        while True:
            counts = run_due(opts['limit'])
            if any(counts.values()) or not opts['loop']:
                self.stdout.write(f"marketing automation: {counts}")
            if not opts['loop']:
                break
            time.sleep(opts['interval'])