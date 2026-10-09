"""Settings lookup: Django settings first, then OS environment, then default.
Lets every CAPI / Meta Lead Ads secret live in environment variables without
having to edit settings.py."""
import os

from django.conf import settings


def get(name, default=''):
    value = getattr(settings, name, None)
    if value in (None, ''):
        value = os.environ.get(name, '')
    return value if value not in (None, '') else default
