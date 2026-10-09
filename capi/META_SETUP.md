# Meta Lead Ads + Conversions API — setup

## Flow
Facebook Lead Form submit → Meta signed webhook `POST /capi/webhook/meta/` → event row stored
(UNIQUE `leadgen_id`) → lead fetched from Graph API with the Page token → CRM Lead created / matched
(Meta lead ID → email → phone) → `LeadTracking` + `LeadTouchpoint` (campaign / ad set / ad / page / form
answers) → CRM Activity entry → existing AI duplicate-check / assignment / scoring → optional follow-up hooks.
CRM stages (qualified, meeting, proposal, won, revenue) → `ConversionEvent` queue → Meta Conversions API.

## Environment variables (or the same names in settings.py)
| Name | Required | Purpose |
|---|---|---|
| `META_APP_SECRET` | yes | Verifies `X-Hub-Signature-256` on every webhook POST; also used for `appsecret_proof`. Without it ALL webhook POSTs are rejected. |
| `META_WEBHOOK_VERIFY_TOKEN` | optional | Handshake token. If unset, the random token auto-generated on the Connections page is used. |
| `CAPI_ENCRYPTION_KEY` | recommended | Fernet key material for stored tokens (falls back to `SECRET_KEY`; changing it invalidates saved tokens). |
| `CAPI_META_ACCESS_TOKEN` | optional | CAPI system-user token fallback (UI-saved token wins). |
| `CAPI_META_PAGE_ACCESS_TOKEN` | optional | Page token fallback (UI-saved token wins). |
| `CAPI_META_API_VERSION` | optional | Graph version for CAPI **and** Lead Ads (default `v25.0`). |
| `CAPI_PUBLIC_BASE_URL` | optional | e.g. `https://crm.example.com` — used to display the callback URL behind a proxy. |
| `CAPI_DEFAULT_CURRENCY`, `CAPI_DEFAULT_PHONE_COUNTRY_CODE` | optional | Existing; `USD` / `92`. |
| `CAPI_INTAKE_SECRET` | optional | Existing generic intake endpoint (Zapier/Make). |
| `CAPI_ACTIVITY_MODEL` | optional | `app_label.Model` of the CRM timeline model. Auto-detected (`leads.Activity`, `LeadActivity`, …) if blank. |
| `CAPI_CAMPAIGN_RESOLVER` | optional | `pkg.mod.fn(lead, touch)` — link the lead to your Campaign/Marketing record. |
| `CAPI_POST_IMPORT_HOOKS` | optional | List / comma string of `pkg.mod.fn(lead, created, touch)` for follow-up automation. |

## Meta permissions
Token owner must be able to run **ADVERTISE** tasks on the Page and the Page must allow the app.
* Lead retrieval + webhook: `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`,
  `pages_read_engagement`, `pages_manage_ads`, `ads_management`.
* Conversions API: `ads_management` (system-user token generated for the Dataset) .
* Standard access works for admins/testers of your own app; Advanced Access (App Review + Business
  Verification) is needed to serve other businesses' Pages.

## Steps
1. Meta App → Webhooks → **Page** → Callback URL = value shown on *Conversion API Center → Connections*;
   Verify token = the one shown/saved there; subscribe field **leadgen**.
2. Connections → Meta: Page ID, Page Access Token, Dataset ID, CAPI token → Save → *Subscribe page to leadgen*
   → *Test page & subscription*.
3. `python manage.py migrate` (0004 is additive only).
4. Schedule `python manage.py process_conversion_queue` every minute (or run with `--loop`): it sends due
   CAPI events **and** retries failed Lead Ads imports.
5. `python manage.py seed_capi_defaults` → disabled starter mappings (Lead, Qualified Lead, Meeting, Proposal,
   Purchase/Won, Revenue). Enable only what you want sent. Use the Meta *Test events* code first.
6. Test with Meta's Lead Ads Testing Tool (developers.facebook.com/tools/lead-ads-testing).

## Notes
* `models1.py` is an old backup copy of `models.py`; Django ignores it and it is safe to delete.
* Retry schedule: 1 min, 5 min, 30 min, 2 h, 12 h; after 6 tries the event shows as *Gave up* under
  *Lead Ads* with a Retry button.
* Campaign / ad set / ad **names** are requested from Graph; if Meta does not return them for an account only
  the IDs are stored (IDs always come from the webhook).
