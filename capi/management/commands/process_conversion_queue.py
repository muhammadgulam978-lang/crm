import time

from django.core.management.base import BaseCommand

from capi.services.meta_webhook import retry_due
from capi.services.queue import process_queue


class Command(BaseCommand):
    help = ('Send due conversion events to Meta / LinkedIn AND retry failed Meta Lead Ads imports. '
            'Schedule every minute, or use --loop.')

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--loop', action='store_true', help='Keep running (Ctrl+C to stop).')
        parser.add_argument('--interval', type=int, default=60)

    def handle(self, *args, **opts):
        while True:
            counts = process_queue(opts['limit'])
            leads = retry_due(opts['limit'])
            if any(counts.values()) or any(leads.values()) or not opts['loop']:
                self.stdout.write(f'CAPI queue: {counts} | Lead Ads retry: {leads}')
            if not opts['loop']:
                break
            time.sleep(opts['interval'])
