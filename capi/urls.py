from django.urls import path

from . import views

app_name = 'capi'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('connections/', views.connections, name='connections'),
    path('connections/<str:platform>/test/', views.connection_test, name='connection_test'),
    path('mapping/', views.config_list, name='config_list'),
    path('mapping/new/', views.config_edit, name='config_create'),
    path('mapping/<int:pk>/edit/', views.config_edit, name='config_edit'),
    path('mapping/<int:pk>/toggle/', views.config_toggle, name='config_toggle'),
    path('mapping/<int:pk>/linkedin-associate/', views.config_associate, name='config_associate'),
    path('events/', views.event_list, name='event_list'),
    path('events/process/', views.process_now, name='process_now'),
    path('events/<int:pk>/', views.event_detail, name='event_detail'),
    path('events/<int:pk>/<str:action>/', views.event_action, name='event_action'),
    path('logs/', views.log_list, name='log_list'),
    path('leads/<int:lead_id>/tracking/', views.lead_tracking, name='lead_tracking'),
    path('intake/<str:platform>/', views.intake, name='intake'),
]
