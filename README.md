# Conversion API Center (Meta CAPI + LinkedIn Conversions API)

Direction of data
- Leads IN : Meta / LinkedIn Lead Sync (or Zapier/Make) -> POST /capi/intake/<meta|linkedin>/ -> CRM Lead (+ attribution, consent)
- Conversions OUT : CRM change -> trigger engine -> queue -> Meta CAPI / LinkedIn CAPI

## Install (Windows / PowerShell)
    pip install -r requirements.txt          # adds cryptography
    python manage.py migrate capi            # migrate ONLY capi (see note below)
    python manage.py seed_capi_defaults      # optional: DISABLED starter Meta mappings
    python manage.py process_conversion_queue --loop   # sender; or schedule it every minute

.env (optional but recommended)
    CAPI_ENCRYPTION_KEY=<long random string>   # encrypts stored tokens
    CAPI_INTAKE_SECRET=<random string>          # enables the lead intake endpoint
    CAPI_DEFAULT_CURRENCY=PKR
    CAPI_DEFAULT_PHONE_COUNTRY_CODE=92

## Use
1. Sidebar -> Conversion API (admin / CEO only)
2. Connections: Meta = Dataset/Pixel ID + token; LinkedIn = ad account ID + token. "Test connection".
3. Event mapping: create/enable mappings (CRM trigger -> platform event). Nothing is sent unless enabled.
4. Per lead: /capi/leads/<id>/tracking/ holds attribution, consent, click IDs (fbc, fbp, li_fat_id).
5. Queue / Sent / Failed / Retry / Logs tabs; every event has payload (hashed), response and attempts.

## Triggers (CRM -> event)
lead created | lead status changed (from/to) | AI score reaches N | opportunity stage changed (from/to)
| meeting booked | quotation approved (proposal accepted) | contract signed/active | deal won
(opportunity Won or lead Converted) | payment recorded (revenue)

## Eligibility (else NOT queued)
mapping enabled + connection active + lead not a duplicate + lead attributed to the same platform
+ consent granted (per mapping) . Duplicate sends are blocked by a unique (mapping, dedup key).
Queued events with no usable identifier are marked "skipped" instead of being sent.

## Notes
- Meta: action_source=system_generated, custom_data.event_source=crm, lead_id for Lead Ads leads,
  hashed em/ph/external_id. Events older than 7 days are rejected by Meta and are failed locally.
- LinkedIn: needs a conversion rule URN per mapping + hashed email or li_fat_id. Names are not sent
  (CAPI_LINKEDIN_SEND_USER_INFO=False).
- Retries: 1m, 5m, 30m, 2h, 12h (5 attempts), then Failed (manual "Retry now").
- Bulk QuerySet.update() calls bypass Django signals and therefore do not trigger events.


# AI Sales Assistant + Product Sales & Offer Matching (native SIS CRM module)

Reuses the existing Leads, Contacts, Companies, Opportunities, Meetings, Follow-ups, Tasks, Communication logs,
Activity log and the Conversion API module. No separate CRM, no duplicate lead/contact tables.

## Install (Windows / PowerShell)
    pip install -r requirements.txt
    python manage.py migrate salesai          # migrate ONLY salesai (same note as capi)
    python manage.py salesai_seed_kb          # optional: 4 DRAFT entries (EduPilot, SIS AI CRM, AI BDO / Automation, Custom Software Development)
    # .env: GROQ_API_KEY=...   (already used by the CRM)   SALESAI_BACKGROUND_ANALYSIS=true  (PostgreSQL only)

## First-time setup (required - the assistant knows nothing about SIS until you tell it)
1. Sidebar > AI Sales Assistant > **Product knowledge base**: fill the approved summary, features, pricing, timeline,
   guarantees, case examples, objections, discovery questions. Empty = shown as "not in the knowledge base".
2. A manager presses **Approve**. Only approved entries are ever recommended. Any later edit returns it to draft.
3. Settings: target countries / industries, near-term window, priority weights.

## What the AI may and may not do
* AI only EXTRACTS facts from CRM records (each with an exact quote). A fact is "verified" only if the quote is found
  word-for-word in the record, numbers match, it is not hedged ("maybe"), and it is stated (not inferred).
  Everything else is an ASSUMPTION - shown, but never used for a recommendation.
* Matching, next action, missing information and priority are computed by rules from verified facts + the approved KB.
* Features, prices, guarantees, timelines and case examples come only from approved KB text.
* Nothing is sent to a customer. Call notes are AI drafts and are saved only after the salesperson approves.

## Where it shows
* Lead screen and Opportunity screen: "AI Product Match" card - Recommended Product, Why Matched, Missing Information,
  Recommended Next Action, and Accept / Override (reason mandatory).
* /sales-ai/ : overview, next actions, customer understanding, call brief, after-conversation drafts,
  AI overrides report (managers), settings, product knowledge base.
