"""Bridges between the Meta import and the rest of the existing CRM.

* log_activity()   -> writes the lead's timeline entry in the CRM's own Activity model.
* run_post_import()-> campaign resolver / follow-up hooks you configure.

The CRM's Activity model lives in the `leads` app, so it is located by introspection
(or pinned with CAPI_ACTIVITY_MODEL = 'leads.Activity'). Every function here is
fail-safe: a problem in the CRM side can never lose an imported lead.
"""
import importlib
import logging

from django.apps import apps
from django.db import transaction

from capi import conf

logger = logging.getLogger(__name__)

ACTIVITY_CANDIDATES = ['leads.Activity', 'leads.LeadActivity', 'leads.Interaction',
                       'leads.TimelineEvent', 'leads.Timeline', 'leads.LeadNote', 'leads.Note']
TITLE_FIELDS = ('title', 'subject', 'summary', 'name')
BODY_FIELDS = ('description', 'notes', 'note', 'details', 'detail', 'content', 'body', 'text',
               'message', 'comment')
KIND_FIELDS = ('activity_type', 'type', 'kind', 'action', 'event_type')
KIND_PREFERENCE = ('meta_lead', 'lead_created', 'created', 'system', 'note', 'other')


def _activity_model():
    configured = conf.get('CAPI_ACTIVITY_MODEL')
    for label in ([configured] if configured else ACTIVITY_CANDIDATES):
        try:
            return apps.get_model(label)
        except (LookupError, ValueError):
            continue
    return None


def _fields(model):
    return {f.name: f for f in model._meta.get_fields()
            if getattr(f, 'concrete', False) and not f.auto_created}


def _first(fields, names):
    return next((n for n in names if n in fields), None)


def build_activity_kwargs(model, lead, title, description):
    """Map (lead, title, description) onto whatever the Activity model calls its fields."""
    fields = _fields(model)
    kwargs = {}
    lead_field = next((n for n, f in fields.items()
                       if getattr(f, 'related_model', None) is lead.__class__), None)
    if not lead_field:
        return None
    kwargs[lead_field] = lead

    title_f, body_f = _first(fields, TITLE_FIELDS), _first(fields, BODY_FIELDS)
    if title_f and body_f:
        kwargs[title_f], kwargs[body_f] = title[:fields[title_f].max_length or 255], description
    elif title_f or body_f:
        name = title_f or body_f
        kwargs[name] = f'{title}\n{description}'.strip()
        if getattr(fields[name], 'max_length', None):
            kwargs[name] = kwargs[name][:fields[name].max_length]
    else:
        return None

    kind_f = _first(fields, KIND_FIELDS)
    if kind_f:
        choices = [c[0] for c in (fields[kind_f].choices or [])]
        if choices:
            pick = next((k for k in KIND_PREFERENCE if k in choices), None)
            if pick:
                kwargs[kind_f] = pick
        else:
            kwargs[kind_f] = 'meta_lead'
            max_len = getattr(fields[kind_f], 'max_length', None)
            if max_len and len('meta_lead') > max_len:
                kwargs[kind_f] = 'note'[:max_len]
    return kwargs


def log_activity(lead, title, description=''):
    """Create the CRM timeline entry. Returns True when it was written."""
    try:
        model = _activity_model()
        if model is None:
            logger.warning('CAPI: no Activity model found - set CAPI_ACTIVITY_MODEL to enable '
                           'timeline entries for imported leads.')
            return False
        kwargs = build_activity_kwargs(model, lead, title, description)
        if kwargs is None:
            logger.warning('CAPI: could not map %s to a lead/title/description - set '
                           'CAPI_ACTIVITY_MODEL to a compatible model.', model.__name__)
            return False
        with transaction.atomic():           # savepoint: a failure must not poison the import
            model.objects.create(**kwargs)
        return True
    except Exception:
        logger.exception('CAPI: could not write timeline entry for lead %s', getattr(lead, 'pk', '?'))
        return False


def _call_dotted(path, *args):
    module, _, attr = path.rpartition('.')
    getattr(importlib.import_module(module), attr)(*args)


def run_post_import(lead, created, touch):
    """Optional project hooks. CAPI_CAMPAIGN_RESOLVER: 'pkg.mod.fn' called as fn(lead, touch)
    to link the lead to your Campaign/Marketing record. CAPI_POST_IMPORT_HOOKS: list of
    'pkg.mod.fn' called as fn(lead, created, touch) for follow-up automation."""
    resolver = conf.get('CAPI_CAMPAIGN_RESOLVER')
    hooks = list(conf.get('CAPI_POST_IMPORT_HOOKS', []) or [])
    if isinstance(hooks, str):
        hooks = [h.strip() for h in hooks.split(',') if h.strip()]
    if resolver:
        try:
            _call_dotted(resolver, lead, touch)
        except Exception:
            logger.exception('CAPI campaign resolver failed for lead %s', lead.pk)
    for path in hooks:
        try:
            _call_dotted(path, lead, created, touch)
        except Exception:
            logger.exception('CAPI post-import hook %s failed for lead %s', path, lead.pk)
