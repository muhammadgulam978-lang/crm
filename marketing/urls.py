from django.urls import path
from . import views

app_name = 'marketing'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # Campaigns
    path('campaigns/', views.campaign_list, name='campaign_list'),
    path('campaigns/new/', views.campaign_create, name='campaign_create'),
    path('campaigns/<int:pk>/edit/', views.campaign_edit, name='campaign_edit'),
    path('campaigns/<int:pk>/delete/', views.campaign_delete, name='campaign_delete'),

    # Sources
    path('sources/', views.source_list, name='source_list'),
    path('sources/new/', views.source_create, name='source_create'),
    path('sources/<int:pk>/edit/', views.source_edit, name='source_edit'),
    path('sources/<int:pk>/delete/', views.source_delete, name='source_delete'),

    # Content
    path('content/', views.content_list, name='content_list'),
    path('content/new/', views.content_create, name='content_create'),
    path('content/<int:pk>/edit/', views.content_edit, name='content_edit'),
    path('content/<int:pk>/delete/', views.content_delete, name='content_delete'),

    # Consent
    path('consent/', views.consent_list, name='consent_list'),

    path('consent/new/', views.consent_create, name='consent_create'),
    path('consent/<int:pk>/edit/', views.consent_edit, name='consent_edit'),
    path('consent/<int:pk>/delete/', views.consent_delete, name='consent_delete'),

    # Suppression
    path('suppression/', views.suppression_list, name='suppression_list'),
    path('suppression/new/', views.suppression_create, name='suppression_create'),
    path('suppression/<int:pk>/edit/', views.suppression_edit, name='suppression_edit'),
    path('suppression/<int:pk>/delete/', views.suppression_delete, name='suppression_delete'),

    # Referrals
    path('referrals/', views.referral_list, name='referral_list'),
    path('referrals/new/', views.referral_create, name='referral_create'),
    path('referrals/<int:pk>/edit/', views.referral_edit, name='referral_edit'),
    path('referrals/<int:pk>/delete/', views.referral_delete, name='referral_delete'),

    # Automation
    path('automation/', views.automation_list, name='automation_list'),
    path('automation/new/', views.automation_create, name='automation_create'),
    path('automation/<int:pk>/edit/', views.automation_edit, name='automation_edit'),
    path('automation/<int:pk>/delete/', views.automation_delete, name='automation_delete'),
    
        # Prospects
    path('prospects/', views.prospect_list, name='prospect_list'),
    path('prospects/new/', views.prospect_create, name='prospect_create'),
    path('prospects/<int:pk>/edit/', views.prospect_edit, name='prospect_edit'),
    path('prospects/<int:pk>/delete/', views.prospect_delete, name='prospect_delete'),
    path('prospects/import/', views.prospect_import, name='prospect_import'),
    path('prospects/import/preview/', views.prospect_import_preview, name='prospect_import_preview'),
    path('prospects/import/template/', views.prospect_import_template, name='prospect_import_template'),
    
    path('reactivation/', views.reactivation_list, name='reactivation_list'),
    
    path('content/calendar/', views.content_calendar, name='content_calendar'),
    path('content/<int:pk>/status/<str:new_status>/', views.content_transition, name='content_transition'),
    
    
        # Public lead form (no auth)
    path('lead-form/', views.public_lead_form, name='public_lead_form'),
    path('lead-form/api/', views.public_lead_form_api, name='public_lead_form_api'),
    
    path('reports/weekly/', views.weekly_report_preview, name='weekly_report_preview'),
    
     
]