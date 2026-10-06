from django.contrib import admin
from .models import (ProspectSource, Campaign, MarketingContent, MarketingConsent,
                     SuppressionEntry, AutomationRun, AutomationStep, Referral)


@admin.register(ProspectSource)
class ProspectSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'is_active', 'cost_per_lead')
    list_filter = ('kind', 'is_active')
    search_fields = ('name',)


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ('name', 'channel', 'status', 'owner', 'start_date', 'end_date')
    list_filter = ('status', 'channel')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(MarketingContent)
class MarketingContentAdmin(admin.ModelAdmin):
    list_display = ('title', 'campaign', 'kind', 'language', 'is_active')
    list_filter = ('kind', 'is_active', 'language')
    search_fields = ('title', 'subject')


@admin.register(MarketingConsent)
class MarketingConsentAdmin(admin.ModelAdmin):
    list_display = ('lead', 'channel', 'purpose', 'status', 'source', 'updated_at')
    list_filter = ('channel', 'purpose', 'status')
    search_fields = ('lead__name', 'lead__email')


@admin.register(SuppressionEntry)
class SuppressionEntryAdmin(admin.ModelAdmin):
    list_display = ('channel', 'value', 'reason', 'expires_at', 'created_at')
    list_filter = ('channel', 'reason')
    search_fields = ('value',)


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'lead', 'campaign', 'status', 'current_step', 'next_run_at')
    list_filter = ('status',)


admin.site.register(AutomationStep)
admin.site.register(Referral)