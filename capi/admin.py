from django.contrib import admin

from .models import (ConversionEvent, ConversionEventConfig, ConversionEventLog,
                     LeadTouchpoint, LeadTracking, PlatformConnection,
                     MetaWebhookEvent, LinkedInWebhookEvent)


@admin.register(PlatformConnection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ('platform', 'is_active', 'health_status', 'token_hint',
                    'token_expires_at', 'page_id', 'webhook_subscribed_at')
    exclude = ('access_token_enc', 'page_access_token_enc')


@admin.register(ConversionEventConfig)
class ConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'connection', 'trigger_key', 'is_enabled')
    list_filter = ('connection', 'is_enabled')


@admin.register(ConversionEvent)
class EventAdmin(admin.ModelAdmin):
    list_display = ('event_name', 'platform', 'lead_label', 'status', 'attempts', 'created_at')
    list_filter = ('platform', 'status')
    search_fields = ('lead_label', 'event_id', 'campaign_name')
    readonly_fields = ('request_payload', 'response_body')


@admin.register(MetaWebhookEvent)
class MetaWebhookEventAdmin(admin.ModelAdmin):
    list_display = ('leadgen_id', 'page_id', 'processed', 'lead', 'created_at')
    list_filter = ('processed',)
    search_fields = ('leadgen_id', 'page_id', 'form_id', 'ad_id')
    readonly_fields = ('leadgen_id', 'page_id', 'form_id', 'ad_id', 'adset_id',
                       'campaign_id', 'raw_payload', 'created_at')


@admin.register(LinkedInWebhookEvent)
class LinkedInWebhookEventAdmin(admin.ModelAdmin):
    
    list_display = ('lead_urn', 'linkedin_lead_id', 'form_urn', 'owner_urn',
                    'processed', 'lead', 'created_at')
    list_filter = ('processed',)
    search_fields = ('lead_urn', 'linkedin_lead_id', 'form_urn', 'form_id',
                     'owner_urn', 'campaign_urn')
    
    readonly_fields = ('lead_urn', 'linkedin_lead_id', 'form_urn', 'form_id', 'owner_urn',
                       'campaign_urn', 'creative_urn', 'share_urn',
                       'raw_payload', 'created_at')


admin.site.register(ConversionEventLog)
admin.site.register(LeadTracking)
admin.site.register(LeadTouchpoint)