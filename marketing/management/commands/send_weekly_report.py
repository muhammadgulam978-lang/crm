"""Send the weekly marketing report by email.

Usage:
    python manage.py send_weekly_report
    python manage.py send_weekly_report --days 14
    python manage.py send_weekly_report --to admin@x.com,ceo@x.com
    python manage.py send_weekly_report --dry-run
"""
from django.core.management.base import BaseCommand

from marketing.services import weekly_report as wr


class Command(BaseCommand):
    help = 'Aggregate the last N days of marketing data and email a report.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--to', type=str, default='',
                            help='Comma-separated recipients. Default: all admin/CEO users.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Collect and print but do not send.')

    def handle(self, *args, **opts):
        days = opts['days']
        self.stdout.write(f'Collecting last {days} days...')
        data = wr.collect(days=days)

        self.stdout.write(self.style.SUCCESS('Collected:'))
        for key, val in data['summary'].items():
            self.stdout.write(f'  {key}: {val}')

        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — no email sent.'))
            return

        recipients = None
        if opts['to']:
            recipients = [e.strip() for e in opts['to'].split(',') if e.strip()]

        sent, error = wr.send(recipients=recipients, days=days)
        if error:
            self.stdout.write(self.style.ERROR(f'Failed: {error}'))
            return
        self.stdout.write(self.style.SUCCESS(f'Sent to {sent} recipient(s).'))