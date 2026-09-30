from django.contrib import admin

from . import models

for m in (models.SalesAssistantSettings, models.SISService, models.CustomerFact, models.LeadInsight, models.NextAction,
          models.OverrideLog, models.CallBrief, models.ConversationDraft, models.AIRun, models.QualificationQuestion):
    admin.site.register(m)
