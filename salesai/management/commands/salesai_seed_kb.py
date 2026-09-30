from django.core.management.base import BaseCommand

from salesai.models import SISService

# STARTING POINTS ONLY. Names and matching keywords are editable suggestions. Nothing customer-facing
# (summary, features, pricing, timelines, guarantees) is written here - SIS must enter and approve that.
SEED = [
    dict(name='EduPilot', kind='product', industries='education\nschool\ncollege\nuniversity',
         keywords='examination\nexam\nresult card\nmulti campus\nschool management\nstudent management\nfee management\ntimetable\nattendance'),
    dict(name='SIS AI CRM', kind='product',
         keywords='crm\nlead management\nlead tracking\nfollow up\nsales pipeline\ncustomer relationship\nlead follow up'),
    dict(name='AI BDO / Automation', kind='product',
         keywords='automated follow up\nautomate follow up\nautomate lead\nlead nurturing\nwhatsapp automation\nbusiness development automation\nautomation'),
    dict(name='Custom Software Development', kind='custom',
         keywords='custom software\nbespoke\ntailor made\nunique requirement\nown software\nmobile app\nweb application'),
]


class Command(BaseCommand):
    help = 'Creates DRAFT knowledge-base entries (EduPilot, SIS AI CRM, AI BDO, Custom Software). Nothing is approved.'

    def handle(self, *args, **opts):
        made = 0
        for row in SEED:
            _, created = SISService.objects.get_or_create(name=row['name'], defaults={**row, 'status': 'draft'})
            made += created
        self.stdout.write(f'{made} draft entr{"y" if made == 1 else "ies"} created. Open Sales AI > Product knowledge base, '
                          f'fill in the approved summary / features / pricing / timeline, then Approve.')
