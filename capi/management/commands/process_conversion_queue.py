import time

from django.core.management.base import BaseCommand

from capi.services.queue import process_queue


class Command(BaseCommand):
    help = 'Send due conversion events to Meta / LinkedIn. Schedule every minute, or use --loop.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--loop', action='store_true', help='Keep running (Ctrl+C to stop).')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **opts):
        while True:
            counts = process_queue(opts['limit'])
            if any(counts.values()) or not opts['loop']:
                self.stdout.write(f'CAPI queue: {counts}')
            if not opts['loop']:
                break
            time.sleep(opts['interval'])
