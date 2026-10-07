class AIAssistantForm(BootstrapMixin, forms.Form):
    CONTENT_KINDS = [
        ('linkedin_post', 'LinkedIn post (organic)'),
        ('facebook_post', 'Facebook post (organic)'),
        ('instagram_post', 'Instagram caption (organic)'),
        ('email', 'Cold outreach email'),
        ('followup_email', 'Follow-up email'),
        ('whatsapp', 'WhatsApp message'),
        ('call_script', 'Call script'),
        ('demo_invite', 'Demo invitation email'),
        ('referral_request', 'Referral request email'),
        ('case_study_outline', 'Case study outline'),
        ('weekly_calendar', 'Weekly content calendar (7 days)'),
    ]

    content_kind = forms.ChoiceField(choices=CONTENT_KINDS, label='What to generate')
    objective = forms.CharField(
        max_length=400, widget=forms.Textarea(attrs={'rows': 3}),
        help_text='One or two sentences describing the goal of this content.',
    )
    industry = forms.CharField(max_length=120, required=False)
    geography = forms.CharField(max_length=120, required=False)
    problem = forms.CharField(max_length=400, required=False, widget=forms.Textarea(attrs={'rows': 2}))
    channel = forms.ChoiceField(
        choices=[('', '—'), ('linkedin', 'LinkedIn'), ('facebook', 'Facebook'),
                 ('instagram', 'Instagram'),