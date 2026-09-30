from django.urls import path

from . import views

app_name = 'salesai'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('next-actions/', views.next_actions, name='next_actions'),
    path('understanding/', views.understanding, name='understanding'),
    path('overrides/', views.overrides, name='overrides'),
    path('settings/', views.assistant_settings, name='settings'),
    path('knowledge-base/', views.knowledge_base, name='kb'),
    path('knowledge-base/save/', views.service_save, name='service_save'),
    path('knowledge-base/<int:pk>/approve/', views.service_approve, name='service_approve'),
    path('knowledge-base/<int:pk>/delete/', views.service_delete, name='service_delete'),
    path('lead/<int:pk>/offer/decide/', views.offer_decide, name='offer_decide'),

    path('lead/<int:pk>/', views.workspace, name='workspace'),
    path('lead/<int:pk>/analyze/', views.analyze, name='analyze'),
    path('lead/<int:pk>/facts/add/', views.fact_add, name='fact_add'),
    path('lead/<int:pk>/facts/<int:fid>/<str:action>/', views.fact_action, name='fact_action'),
    path('lead/<int:pk>/question/<int:qid>/<str:action>/', views.question_action, name='question_action'),
    path('lead/<int:pk>/recommendation/accept/', views.recommendation_accept, name='recommendation_accept'),
    path('lead/<int:pk>/recommendation/dismiss/', views.recommendation_dismiss, name='recommendation_dismiss'),
    path('lead/<int:pk>/next-action/', views.next_action_set, name='next_action_set'),
    path('next-action/<int:pk>/done/', views.next_action_done, name='next_action_done'),
    path('lead/<int:pk>/priority/', views.priority_decide, name='priority_decide'),
    path('lead/<int:pk>/close-out-of-market/', views.close_out, name='close_out'),
    path('lead/<int:pk>/brief/', views.brief_view, name='brief'),
    path('brief/<int:pk>/reviewed/', views.brief_reviewed, name='brief_reviewed'),
    path('lead/<int:pk>/after-call/', views.after_call, name='after_call'),
    path('drafts/<int:pk>/', views.draft_review, name='draft_review'),
    path('drafts/<int:pk>/approve/', views.draft_approve, name='draft_approve'),
    path('drafts/<int:pk>/discard/', views.draft_discard, name='draft_discard'),
    
    path('lead/<int:pk>/scope-approval/new/', views.scope_approval_new, name='scope_approval_new'),
    path('scope-approval/<int:pk>/', views.scope_approval_detail, name='scope_approval_detail'),
    path('scope-approval/<int:pk>/sales-approve/', views.scope_approval_sales, name='scope_approval_sales'),
    path('scope-approval/<int:pk>/delivery-approve/', views.scope_approval_delivery, name='scope_approval_delivery'),
    path('scope-approval/<int:pk>/reject/', views.scope_approval_reject, name='scope_approval_reject'),
]
