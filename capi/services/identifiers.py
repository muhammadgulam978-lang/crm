"""Identifier normalisation + hashing. Only what the platforms need is produced."""
import hashlib
import re

from django.conf import settings


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def normalize_email(email):
    email = (email or '').strip().lower()
    return email if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) else None


def normalize_phone(phone, default_cc=None):
    """Digits only, with country code, no leading + or 0 (Meta's format).
    '0300 1234567' -> '923001234567' when default country code is 92."""
    raw = (phone or '').strip()
    if not raw:
        return None
    default_cc = default_cc or getattr(settings, 'CAPI_DEFAULT_PHONE_COUNTRY_CODE', '92')
    digits = re.sub(r'\D', '', raw)
    if not digits:
        return None
    if raw.startswith('+'):
        pass
    elif digits.startswith('00'):
        digits = digits[2:]
    elif digits.startswith('0'):
        digits = default_cc + digits[1:]
    elif len(digits) <= 10:
        digits = default_cc + digits
    return digits if 8 <= len(digits) <= 15 else None


def hashed_email(email):
    n = normalize_email(email)
    return sha256_hex(n) if n else None


def hashed_phone(phone):
    n = normalize_phone(phone)
    return sha256_hex(n) if n else None


def external_id_for(lead):
    prefix = getattr(settings, 'CAPI_EXTERNAL_ID_PREFIX', 'sis-crm-lead-')
    return f'{prefix}{lead.pk}'


def hashed_external_id(lead):
    return sha256_hex(external_id_for(lead))


def split_name(full_name):
    parts = (full_name or '').strip().split()
    if len(parts) < 2:
        return None, None
    return parts[0].lower(), ' '.join(parts[1:]).lower()
