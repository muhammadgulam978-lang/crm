"""
CSV/Excel prospect import with validation, duplicate detection, and preview.

Flow:
  1. parse_file(f)           -> list of raw row dicts
  2. validate_rows(rows)     -> list of ValidatedRow (ok | dup | invalid | skipped)
  3. import_rows(rows, ...)  -> creates/links Lead rows inside a transaction
"""
import csv
import io
import re
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from leads.models import Lead
from ..models import ProspectSource


# Mapping of common header variants -> canonical field name
HEADER_ALIASES = {
    'name':              ['name', 'full name', 'contact name', 'person', 'contact person'],
    'company':           ['company', 'company name', 'organization', 'organisation', 'business'],
    'email':             ['email', 'e-mail', 'email address', 'business email', 'work email'],
    'contact_number':    ['phone', 'contact number', 'mobile', 'phone number', 'business phone',
                          'whatsapp', 'cell', 'telephone'],
    'country':           ['country', 'nation'],
    'city':              ['city', 'town'],
    'address':           ['address', 'street', 'location'],
    'industry':          ['industry', 'sector', 'vertical'],
    'website':           ['website', 'url', 'web', 'company website'],
    'source_url':        ['source url', 'source', 'source link', 'linkedin', 'linkedin url'],
    'business_requirement': ['requirement', 'business requirement', 'need', 'pain point',
                             'requirement details', 'notes'],
    'detail':            ['detail', 'details', 'description', 'notes'],
}

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


@dataclass
class ValidatedRow:
    row_number: int
    raw: dict
    cleaned: dict = field(default_factory=dict)
    status: str = 'ok'          # ok | duplicate | invalid | skipped
    issues: list = field(default_factory=list)
    duplicate_of: int | None = None     # Lead pk of the matching record


# --------------------------------------------------------------- parse

def _canonical_headers(headers):
    """Map arbitrary header text to canonical field names."""
    mapping = {}
    for idx, h in enumerate(headers):
        key = (h or '').strip().lower()
        for canon, aliases in HEADER_ALIASES.items():
            if key in aliases:
                mapping[idx] = canon
                break
    return mapping


def parse_file(uploaded_file):
    """Return (headers, rows) where rows is a list of dicts keyed by canonical names."""
    name = uploaded_file.name.lower()
    if name.endswith('.csv'):
        return _parse_csv(uploaded_file)
    if name.endswith('.xlsx') or name.endswith('.xls'):
        return _parse_xlsx(uploaded_file)
    raise ValueError('Unsupported file type')


def _parse_csv(f):
    raw = f.read()
    # Try UTF-8, fall back to latin-1 for Excel exports
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    reader = csv.reader(io.StringIO(text))
    all_rows = list(reader)
    if not all_rows:
        return [], []
    headers = all_rows[0]
    canon = _canonical_headers(headers)
    rows = []
    for i, r in enumerate(all_rows[1:], start=2):
        if not any((c or '').strip() for c in r):
            continue
        row = {canon[j]: (r[j] or '').strip() for j in canon if j < len(r)}
        row['__row__'] = i
        rows.append(row)
    return headers, rows


def _parse_xlsx(f):
    """Uses openpyxl if installed. Falls back to a helpful error."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise ValueError('openpyxl is not installed. Run: pip install openpyxl')
    wb = load_workbook(f, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        headers = [str(h or '') for h in next(rows_iter)]
    except StopIteration:
        return [], []
    canon = _canonical_headers(headers)
    rows = []
    for i, r in enumerate(rows_iter, start=2):
        if not any((str(c or '').strip() for c in r)):
            continue
        row = {canon[j]: str(r[j] or '').strip() for j in canon if j < len(r)}
        row['__row__'] = i
        rows.append(row)
    return headers, rows


# --------------------------------------------------------------- validate

def _norm_phone(v):
    return re.sub(r'\D', '', v or '')


def _norm_email(v):
    return (v or '').strip().lower()


def validate_rows(rows):
    """Return list of ValidatedRow. Does not write to DB."""
    out = []
    seen_emails = set()
    seen_phones = set()

    for raw in rows:
        v = ValidatedRow(row_number=raw.get('__row__', 0), raw=raw)

        name = (raw.get('name') or '').strip()
        email = _norm_email(raw.get('email', ''))
        phone_raw = raw.get('contact_number', '')
        phone = _norm_phone(phone_raw)

        # --- invalid checks
        if not name and not email and not phone:
            v.status = 'invalid'
            v.issues.append('Row is empty (no name, email or phone).')
            out.append(v)
            continue

        if not name:
            # Fall back to email local-part or phone tail as display name
            if email:
                name = email.split('@')[0].title()
            elif phone:
                name = f'Prospect {phone[-4:]}'

        if email and not EMAIL_RE.match(email):
            v.status = 'invalid'
            v.issues.append(f'Invalid email: {email}')
            out.append(v)
            continue

        if not email and not phone:
            v.status = 'invalid'
            v.issues.append('Either email or phone is required.')
            out.append(v)
            continue

        # --- duplicate checks within this file
        if email and email in seen_emails:
            v.status = 'duplicate'
            v.issues.append(f'Duplicate email in file: {email}')
            out.append(v)
            continue
        if phone and phone in seen_phones:
            v.status = 'duplicate'
            v.issues.append(f'Duplicate phone in file: {phone}')
            out.append(v)
            continue

        # --- duplicate checks against existing CRM leads
        existing = None
        if email:
            existing = Lead.objects.filter(email__iexact=email).first()
        if existing is None and phone:
            tail = phone[-9:]
            # fuzzy match on last 9 digits
            for lead in Lead.objects.filter(contact_number__icontains=tail[-7:]).only('id', 'contact_number'):
                if _norm_phone(lead.contact_number).endswith(tail):
                    existing = lead
                    break
        if existing is not None:
            v.status = 'duplicate'
            v.duplicate_of = existing.pk
            v.issues.append(f'Matches existing lead #{existing.pk} ({existing.name})')
            seen_emails.add(email)
            seen_phones.add(phone)
            out.append(v)
            continue

        # --- mark as OK
        v.status = 'ok'
        v.cleaned = {
            'name': name,
            'email': email or '',
            'contact_number': phone_raw.strip(),
            'country': (raw.get('country') or '').strip(),
            'city': (raw.get('city') or '').strip(),
            'address': (raw.get('address') or '').strip(),
            'source_url': (raw.get('source_url') or '').strip(),
            'business_requirement': (raw.get('business_requirement') or '').strip(),
            'detail': (raw.get('detail') or '').strip(),
            # company / industry / website can be stashed in detail for now
            '_company': (raw.get('company') or '').strip(),
            '_industry': (raw.get('industry') or '').strip(),
            '_website': (raw.get('website') or '').strip(),
        }
        seen_emails.add(email)
        seen_phones.add(phone)
        out.append(v)

    return out


# --------------------------------------------------------------- import

def _build_detail(cleaned):
    parts = []
    if cleaned.get('_company'):
        parts.append(f"Company: {cleaned['_company']}")
    if cleaned.get('_industry'):
        parts.append(f"Industry: {cleaned['_industry']}")
    if cleaned.get('_website'):
        parts.append(f"Website: {cleaned['_website']}")
    if cleaned.get('business_requirement'):
        parts.append(f"Requirement: {cleaned['business_requirement']}")
    if cleaned.get('detail'):
        parts.append(cleaned['detail'])
    return '\n'.join(parts) or None


@transaction.atomic
def import_rows(validated_rows, *, source=None, default_status='new', user=None):
    """
    Create Lead rows for every ValidatedRow with status == 'ok'.
    Returns counts dict.
    """
    counts = {'created': 0, 'linked': 0, 'skipped_duplicate': 0, 'skipped_invalid': 0}

    for v in validated_rows:
        if v.status == 'invalid':
            counts['skipped_invalid'] += 1
            continue
        if v.status == 'duplicate':
            counts['skipped_duplicate'] += 1
            continue

        cleaned = v.cleaned
        lead = Lead.objects.create(
            name=cleaned['name'],
            email=cleaned['email'] or None,
            contact_number=cleaned['contact_number'] or '',
            country=cleaned['country'] or None,
            city=cleaned['city'] or None,
            address=cleaned['address'] or None,
            detail=_build_detail(cleaned),
            status=default_status,
            lead_source='advertisement',   # neutral default; source is on marketing_source
            marketing_source=source,
            source_url=cleaned['source_url'] or None,
            research_date=timezone.localdate(),
            business_requirement=cleaned['business_requirement'] or None,
        )
        counts['created'] += 1

    return counts