# Generated for LinkedIn Lead Sync integration
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('capi', '0004_meta_lead_ads_reliability'),
        ('leads', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='platformconnection',
            name='linkedin_organization_urn',
            field=models.CharField(
                blank=True, max_length=128,
                verbose_name='LinkedIn Organization URN',
                help_text='urn:li:organization:<id> — owner of the Lead Gen Forms.',
            ),
        ),
        migrations.AddField(
            model_name='platformconnection',
            name='linkedin_webhook_verify_token',
            field=models.CharField(
                blank=True, max_length=64,
                help_text='Random string echoed back to LinkedIn during the webhook challenge.',
            ),
        ),
        migrations.CreateModel(
            name='LinkedInWebhookEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('lead_urn', models.CharField(db_index=True, max_length=128, unique=True, verbose_name='LinkedIn Lead URN')),
                ('linkedin_lead_id', models.CharField(blank=True, db_index=True, max_length=64, verbose_name='LinkedIn Lead ID')),
                ('form_urn', models.CharField(blank=True, max_length=128, verbose_name='Form URN')),
                ('form_id', models.CharField(blank=True, max_length=64, verbose_name='Form ID')),
                ('owner_urn', models.CharField(blank=True, max_length=128, verbose_name='Owner URN')),
                ('campaign_urn', models.CharField(blank=True, max_length=128)),
                ('creative_urn', models.CharField(blank=True, max_length=128)),
                ('share_urn', models.CharField(blank=True, max_length=128)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('processed', models.BooleanField(db_index=True, default=False)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('lead', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='linkedin_webhook_events',
                    to='leads.lead',
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'LinkedIn Lead Gen event',
            },
        ),
    ]