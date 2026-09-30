from django.contrib import admin

from .models import (ConversionEvent, ConversionEventConfig, ConversionEventLog,
                     LeadTouchpoint, LeadTracking, PlatformConnection)


@admin.register(PlatformConnection)
class ConnectionAdmin(admin.ModelAdmin):
    list_display = ('platform', 'is_active', 'health_status', 'token_hint', 'token_expires_at')
    exclude = ('access_token_enc',)  # token is managed from the Conversion API Center only


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


admin.site.register(ConversionEventLog)
admin.site.register(LeadTracking)
admin.site.register(LeadTouchpoint)
