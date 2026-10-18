# ASTRA FEST 2026 — Registration backend

Flask + Supabase (PostgreSQL + private Storage) + Razorpay. Your original registration page keeps its design, animations,
steps, validation and event list; it now talks to real APIs. A protected admin panel lives at `/admin`.

## What was verified vs. what you must do
- Verified here: 54 automated tests pass (`pytest`); `database/schema.sql` + `seed_events.sql` load on PostgreSQL 16 and the
  SQL functions were exercised (valid team, idempotent replay, wrong team size, duplicate email, full event, closed event,
  reservation expiry, paying twice, amount mismatch).
- **Not** verified (needs your accounts): live Supabase Storage uploads/signed URLs, real Razorpay checkout/webhooks, e-mail,
  deployment. Do the test-mode run in the guide below before going live.
- The Supabase `storage` and role names (`anon`, `authenticated`, `service_role`) were stubbed locally; they exist in a real project.

## Files
```
app.py config.py extensions.py supabase_client.py requirements.txt .env.example
database/schema.sql  database/seed_events.sql  database/migrations/
services/   registration_service.py payment_service.py storage_service.py email_service.py pdf_service.py
routes/     public.py registration.py payments.py admin.py
templates/  home.html register.html confirmation.html verify.html admin/*.html
static/     css/admin.css  js/admin.js
tests/      conftest.py test_registration.py test_payments.py test_admin.py
```
`templates/home.html` is a placeholder — keep your own home page and link it to `/register` and `/admin/login`.

## Which keys are secret
| Variable | Where it may live |
|---|---|
| `FLASK_SECRET_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `SMTP_PASSWORD` | **Server only.** Never in HTML/JS, never in Git. |
| `RAZORPAY_KEY_ID` | Public by design. The backend sends it to the checkout page. |
| `SUPABASE_ANON_KEY` | Not used by this app. Leave empty. |

The browser never talks to Supabase directly. All tables have RLS enabled with **no policies**, so even the public key can read nothing.

## Setup guide (beginner friendly)
1. **Create a project**: supabase.com → New project. Save the database password somewhere safe.
2. **Find URL and keys**: Project Settings → **API Keys**. The URL is on the Connect dialog / Data API settings.
   Use the **Secret key** (`sb_secret_…`) for the server. (Older projects: the legacy `service_role` key, on the *Legacy API Keys* tab — Supabase is deprecating those.)
3. **Environment**: `cp .env.example .env`, fill the values. Generate the Flask secret with
   `python -c "import secrets;print(secrets.token_hex(32))"`. Make sure `.env` is in `.gitignore` (it is).
4. **Run the schema**: SQL Editor → New query → paste `database/schema.sql` → Run. It is safe to re-run.
5. **Private bucket**: the schema creates `astra-documents` (private, 5 MB, JPG/PNG/PDF). Confirm in Storage that it shows **Private**.
6. **Security policies**: already in the schema — RLS on every table, no policies, no Storage policies, so only the server key can touch data or files.
7. **Seed events**: SQL Editor → paste `database/seed_events.sql` → Run (52 events; fees are 0 and capacity unlimited until you set them in `/admin/events`; events marked "not open"/unconfirmed stay `draft`).
8. **First admin**: after step 9 run `flask --app app create-admin` (asks for e-mail, password ≥12 chars, role). Roles: `superadmin` (everything), `manager` (events, registrations, payments, exports; **no ID documents**), `verifier` (documents + read registrations).
9. **Install & run locally**
   ```bash
   python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   flask --app app run --debug                             # http://127.0.0.1:5000/register
   ```
10. **Test registration**: set one event to `open` with a small fee in `/admin/events`, register, then check Table Editor → `registrations` and `participants`.
11. **Test documents**: upload a JPG/PNG/PDF on the last step. In Storage you should see `<registration-id>/<uuid>.jpg` (never the original filename). In `/admin/documents`, **View** opens a 60-second signed link.
12. **Razorpay test mode**: dashboard (Test Mode on) → Account & Settings → API Keys → generate; put `rzp_test_…` id and secret in `.env`. Keep payment capture on **automatic** (the code also captures "authorized" payments).
13. **Webhook**: Settings → Webhooks → Add. URL `https://<public-url>/api/astra/payments/webhook` (locally use a tunnel such as ngrok/cloudflared). Choose a secret, copy it to `RAZORPAY_WEBHOOK_SECRET`. Events: `payment.captured`, `payment.failed`, `order.paid`, `refund.processed`.
14. **Scenarios**: success (test card/UPI from Razorpay's test docs), failure (use their failing test method), delayed (close the tab right after paying — the confirmation page polls and the webhook completes it), refund (refund in dashboard → status becomes *refunded*), webhook retry (resend from dashboard → no duplicate).
15. **Deploy** (Render / Railway / any VM): start command `gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT`. Flask serves the frontend too, so one deployment covers both. For a separate frontend host, set `API` in `register.html` to the full backend URL and add that origin to `CORS_ORIGINS`.
16. **Production env**: set every variable from `.env.example` on the host; `FLASK_ENV=production`, `APP_BASE_URL=https://your-domain` (used in QR codes), HTTPS only. For several server instances set `RATELIMIT_STORAGE_URI` to a Redis URL.
17. **Final checklist** — below.

## Switching Razorpay to live
Complete Razorpay KYC → generate **live** keys → replace `RAZORPAY_KEY_ID/SECRET` → create the webhook again in Live mode with a new secret → make one real ₹1–₹10 payment on a test event, refund it, confirm the confirmation card says PAID and the admin Payments page matches → only then publish fees.

## How the important rules are enforced
- **Fee**: read from `events.fee_paise` in the database; `create-order` takes no amount from the browser.
- **Paid = verified**: the payment becomes *paid* only after (a) the HMAC signature checks out, (b) Razorpay's API confirms order id, amount, currency and `captured`, or (c) a webhook signed over the **raw body**. Amount/currency are re-checked in SQL.
- **Idempotency**: unique `razorpay_payment_id`, `webhook_events(event_id)`, SQL `astra_mark_paid` returns early when already paid, one open order per registration (partial unique index), submission `idempotency_key` for double-clicks/retries.
- **Seats**: `astra_create_registration` locks the event row, so two people cannot take the last seat. Unpaid seats are held for `reservation_minutes` (Settings), then released.
- **Late payment edge case**: if money arrives after the hold expired *and* the event filled, the payment is kept, the registration is flagged **review** in the admin list — refund it from the Razorpay dashboard (refund webhooks update the status). Same flag appears for a second payment on an already-paid registration.
- **Documents**: JPG/PNG/PDF, ≤5 MB, extension + MIME + magic bytes + Pillow check, PDFs with active content rejected, server-generated names, private bucket, signed URLs (60 s), every view audited. Rejected documents can be re-uploaded from the confirmation page. Aadhaar is **off** (`ALLOW_AADHAAR_MASKED=false`); if enabled only a *masked* document type is accepted — never ask for or display a full number.
- **Retention**: `flask --app app purge-documents` deletes ID documents older than *Settings → Delete ID documents after (days)*. Schedule it daily (cron / Render cron job).
- **QR**: holds only `https://<site>/verify/<random-token>`; the verify page shows code, event, status, payment status and a masked name.
- **Admin**: server-side session auth, scrypt password hashes, login rate limit (5/min), CSRF on every write, role checks on every API, audit log (`audit_log` table), CSV formula-injection protection. Manual payment marking is **off** by default; when enabled in Settings only a superadmin can use it, with a written reason, and it is audited.
- **Headers/cookies**: HttpOnly + SameSite=Lax + Secure (production) cookies, CSP, `X-Frame-Options`, HSTS, `no-store` on private pages. The CSP allows inline scripts because your registration page is a single HTML file.

## Backups
Supabase → Database → Backups (daily backups on paid plans; the free plan has none). Free-plan alternative: run `pg_dump "<connection string>" > astra-$(date +%F).sql` daily, plus the admin CSV exports before the event. Storage objects are not part of database backups — download the bucket separately if you need it.

## Final security / end-to-end checklist
- [ ] `.env` not committed; no secret appears in page source or browser network tab
- [ ] Individual and team registrations appear correctly in `registrations` / `participants`
- [ ] Wrong team size, closed event and full event are refused (try via the browser console too)
- [ ] ID upload works; the object is in the **private** bucket; opening its plain URL fails
- [ ] `/admin/*` and `/admin/api/*` return 401/redirect when logged out; a `manager` cannot open Documents
- [ ] Test payment succeeds, fails and arrives late; webhook resend creates no duplicates
- [ ] Printed confirmation fits A4, shows the right payment status; pending/failed shows the red warning
- [ ] Dashboard numbers equal the Table Editor counts
- [ ] Production uses HTTPS and `FLASK_ENV=production`
- [ ] Fees, capacities and open/close times confirmed with the committee, then set in `/admin/events`

## API
`GET /api/astra/{config,categories,events,events/<id>}` · `POST /api/astra/register` · `POST|GET /api/astra/registrations/<id>/documents` ·
`POST /api/astra/registrations/<id>/create-order` · `POST /api/astra/payments/verify` · `POST /api/astra/payments/webhook` ·
`GET /api/astra/registrations/<id>/confirmation[.pdf]` · `GET /api/astra/registrations/verify/<token>`.
Registrant endpoints need header `X-Registration-Token` (a random secret generated by the browser; only its SHA-256 is stored).
The confirmation link keeps that secret in the URL `#fragment`, so servers and logs never see it. Admin APIs live under `/admin/api/*`.

## Known limits
- Department in the form stays free text (as in your page); the `departments` table is managed in the admin panel and can be attached to events.
- Fee/e-mail/refund initiation is not done from the admin panel: issue refunds in Razorpay; the webhook updates the record.
- Documents are required only before *paid* checkout; for free events the committee verifies them in `/admin/documents`.
