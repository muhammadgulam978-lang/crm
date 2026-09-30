from django.core.management.base import BaseCommand

from capi import constants as C
from capi.models import ConversionEventConfig, PlatformConnection

# name, trigger, from, to, meta event name, send_value
DEFAULTS = [
    ('Lead', C.TRIGGER_LEAD_CREATED, '', '', 'Lead', False),
    ('Qualified Lead', C.TRIGGER_OPP_STAGE, '', 'qualified', 'QualifiedLead', False),
    ('Appointment', C.TRIGGER_MEETING, '', '', 'Appointment', False),
    ('Application', C.TRIGGER_OPP_STAGE, '', 'proposal', 'Application', False),
    ('Contract', C.TRIGGER_CONTRACT_SIGNED, '', '', 'Contract', True),
    ('Purchase / Won', C.TRIGGER_DEAL_WON, '', '', 'Purchase', True),
    ('Revenue', C.TRIGGER_REVENUE, '', '', 'Revenue', True),
]


class Command(BaseCommand):
    help = 'Create DISABLED starter Meta event mappings. Nothing is sent until an admin enables them.'

    def handle(self, *args, **opts):
        meta, _ = PlatformConnection.objects.get_or_create(platform=C.PLATFORM_META, defaults={'name': 'Meta'})
        PlatformConnection.objects.get_or_create(platform=C.PLATFORM_LINKEDIN, defaults={'name': 'LinkedIn'})
        n = 0
        for name, trig, frm, to, ev, val in DEFAULTS:
            _, created = ConversionEventConfig.objects.get_or_create(
                connection=meta, name=f'{name} (Meta)',
                defaults=dict(trigger_key=trig, from_value=frm, to_value=to, platform_event_name=ev,
                              send_value=val, is_enabled=False))
            n += created
        self.stdout.write(self.style.SUCCESS(f'{n} starter mapping(s) created (all disabled).'))
