-- ASTRA FEST 2026 — schema. Run once in Supabase Dashboard > SQL Editor.
-- Safe to re-run: uses IF NOT EXISTS / OR REPLACE where possible.

create extension if not exists pgcrypto;

-- ---------- reference tables ----------
create table if not exists departments (
  id serial primary key,
  name text not null unique,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists events (
  id serial primary key,
  name text not null,
  category text not null,
  subcategory text not null default '',
  description text not null default '',
  department_id int references departments(id) on delete set null,
  allow_individual boolean not null default false,
  allow_team boolean not null default false,
  min_team_size int check (min_team_size is null or min_team_size >= 1),
  max_team_size int check (max_team_size is null or max_team_size >= 1),
  max_substitutes int not null default 0 check (max_substitutes >= 0),
  fee_paise int not null default 0 check (fee_paise >= 0),     -- 100 paise = Rs 1. Admin-editable.
  capacity int check (capacity is null or capacity >= 0),       -- null = unlimited
  registration_opens_at timestamptz,
  registration_closes_at timestamptz,
  registration_status text not null default 'draft' check (registration_status in ('draft','open','closed')),
  extra_fields jsonb not null default '[]'::jsonb,              -- event-specific form fields
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (allow_individual or allow_team),
  check (min_team_size is null or max_team_size is null or min_team_size <= max_team_size),
  unique (category, subcategory, name)
);
create index if not exists events_category_idx on events(category);

create table if not exists app_settings (
  key text primary key,
  value jsonb not null,
  updated_at timestamptz not null default now()
);
insert into app_settings(key, value) values
  ('require_documents', 'true'),        -- college ID needed before payment
  ('manual_approval', 'false'),         -- admin must approve every registration
  ('allow_manual_payment', 'false'),    -- screenshot-based manual payment review
  ('reservation_minutes', '15'),        -- how long a seat is held while paying
  ('document_retention_days', '90')     -- 'flask purge-documents' deletes ID documents older than this
on conflict (key) do nothing;

-- ---------- registrations ----------
create sequence if not exists astra_reg_seq start 1;

create table if not exists registrations (
  id uuid primary key default gen_random_uuid(),
  reg_code text not null unique,
  event_id int not null references events(id),
  format text not null check (format in ('individual','team')),
  team_name text,
  college_name text not null,
  contingent_code text,
  contact_email text not null,
  contact_phone text not null,
  extra jsonb not null default '{}'::jsonb,
  status text not null default 'pending' check (status in ('pending','confirmed','rejected','cancelled','waitlisted')),
  payment_status text not null default 'not_required'
    check (payment_status in ('not_required','pending','paid','failed','cancelled','refunded')),
  requires_approval boolean not null default false,
  needs_review boolean not null default false,       -- e.g. payment arrived after the seat expired
  reserved_until timestamptz,
  consent boolean not null check (consent),
  consent_at timestamptz not null default now(),
  verification_token text not null unique,           -- random; the only thing inside the QR code
  access_token_hash text not null,                   -- sha256 of the secret the registrant's browser holds
  idempotency_key text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists reg_event_idx on registrations(event_id, status);
create index if not exists reg_email_idx on registrations(lower(contact_email));
create index if not exists reg_payment_idx on registrations(payment_status);

create table if not exists participants (
  id uuid primary key default gen_random_uuid(),
  registration_id uuid not null references registrations(id) on delete cascade,
  full_name text not null,
  email text,
  mobile text,
  department text not null,
  course_year text not null,
  roll_number text not null,
  role text not null check (role in ('individual','captain','member','substitute')),
  created_at timestamptz not null default now()
);
create index if not exists participants_reg_idx on participants(registration_id);

-- ---------- payments ----------
create table if not exists payments (
  id uuid primary key default gen_random_uuid(),
  registration_id uuid not null references registrations(id),
  razorpay_order_id text unique,
  razorpay_payment_id text unique,
  amount_paise int not null check (amount_paise >= 0),
  currency text not null default 'INR',
  status text not null default 'created' check (status in ('created','paid','failed','cancelled','refunded')),
  method text,
  verified_at timestamptz,
  refund_status text,
  last_webhook_event_id text,
  failure_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists payments_reg_idx on payments(registration_id);
-- only one open (unpaid) order per registration -> no double orders
create unique index if not exists one_open_order on payments(registration_id) where status = 'created';

create table if not exists webhook_events (   -- idempotency for Razorpay webhook retries
  event_id text primary key,
  event_type text not null,
  received_at timestamptz not null default now()
);

-- ---------- documents ----------
create table if not exists registration_documents (
  id uuid primary key default gen_random_uuid(),
  registration_id uuid not null references registrations(id) on delete cascade,
  participant_id uuid references participants(id) on delete cascade,
  doc_type text not null check (doc_type in ('college_id','identity_proof','payment_screenshot','aadhaar_masked')),
  storage_path text not null,                       -- private bucket object path only
  content_type text not null,
  size_bytes int not null,
  status text not null default 'pending' check (status in ('pending','verified','rejected')),
  review_notes text,
  reviewed_by uuid,
  reviewed_at timestamptz,
  uploaded_at timestamptz not null default now(),
  deleted_at timestamptz
);
create index if not exists docs_reg_idx on registration_documents(registration_id);
create index if not exists docs_status_idx on registration_documents(status);

-- ---------- admin ----------
create table if not exists admin_users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  role text not null default 'manager' check (role in ('superadmin','manager','verifier')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  last_login_at timestamptz
);

create table if not exists audit_log (
  id bigserial primary key,
  admin_id uuid,
  admin_email text,
  action text not null,
  target_type text,
  target_id text,
  detail jsonb,
  ip text,
  created_at timestamptz not null default now()
);
create index if not exists audit_created_idx on audit_log(created_at desc);

-- ---------- helper: keep updated_at fresh ----------
create or replace function astra_touch() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;
do $$ declare t text; begin
  foreach t in array array['events','registrations','payments','app_settings'] loop
    execute format('drop trigger if exists trg_touch on %I', t);
    execute format('create trigger trg_touch before update on %I for each row execute function astra_touch()', t);
  end loop; end $$;

-- ---------- capacity helpers ----------
create or replace function astra_active_count(p_event_id int) returns int
language sql stable as $$
  select count(*)::int from registrations r
  where r.event_id = p_event_id and r.status in ('pending','confirmed')
    and not (r.payment_status in ('pending','failed','cancelled')
             and (r.reserved_until is null or r.reserved_until < now()));
$$;

create or replace function astra_expire_reservations() returns void
language sql as $$
  update registrations set status = 'cancelled', payment_status = 'cancelled'
  where status = 'pending' and payment_status in ('pending','failed') and reserved_until < now();
$$;

-- ---------- create a registration atomically (capacity + duplicate checks under a row lock) ----------
create or replace function astra_create_registration(p jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare
  ev events%rowtype; ex registrations%rowtype;
  rid uuid; code text; fee int; st text; pst text; fmt text;
  n_main int; n_subs int; lo int; hi int; part jsonb; minutes int; res timestamptz;
  parts jsonb := '[]'::jsonb; pid uuid; approval boolean; max_subs int;
begin
  select * into ex from registrations where idempotency_key = p->>'idempotency_key';
  if found then
    if ex.access_token_hash <> p->>'access_token_hash' then raise exception 'IDEMPOTENCY_CONFLICT'; end if;
    select jsonb_agg(jsonb_build_object('id', id, 'name', full_name, 'role', role)) into parts
      from participants where registration_id = ex.id;
    return jsonb_build_object('id', ex.id, 'reg_code', ex.reg_code, 'status', ex.status,
      'payment_status', ex.payment_status, 'fee_paise', (select fee_paise from events where id = ex.event_id),
      'participants', coalesce(parts, '[]'::jsonb), 'replayed', true);
  end if;

  perform astra_expire_reservations();

  select * into ev from events where id = (p->>'event_id')::int for update;   -- serialises seat checks per event
  if not found or not ev.is_active then raise exception 'EVENT_NOT_FOUND'; end if;
  if ev.registration_status <> 'open'
     or (ev.registration_opens_at is not null and now() < ev.registration_opens_at)
     or (ev.registration_closes_at is not null and now() > ev.registration_closes_at)
  then raise exception 'EVENT_CLOSED'; end if;

  fmt := p->>'format';
  if (fmt = 'individual' and not ev.allow_individual) or (fmt = 'team' and not ev.allow_team)
     or fmt not in ('individual','team') then raise exception 'FORMAT_NOT_ALLOWED'; end if;

  select count(*) filter (where x->>'role' <> 'substitute'), count(*) filter (where x->>'role' = 'substitute')
    into n_main, n_subs from jsonb_array_elements(p->'participants') x;
  if fmt = 'individual' then
    lo := 1; hi := 1;
  else
    if ev.min_team_size is null or ev.max_team_size is null then raise exception 'EVENT_CLOSED'; end if;
    lo := case when ev.allow_individual then greatest(2, ev.min_team_size) else ev.min_team_size end;
    hi := ev.max_team_size;
  end if;
  if n_main < lo or n_main > hi then raise exception 'INVALID_TEAM_SIZE'; end if;
  max_subs := case when fmt = 'team' then ev.max_substitutes else 0 end;
  if n_subs > max_subs then raise exception 'TOO_MANY_SUBSTITUTES'; end if;

  if exists (select 1 from registrations where event_id = ev.id and lower(contact_email) = lower(p->>'contact_email')
             and status in ('pending','confirmed')) then raise exception 'DUPLICATE_REGISTRATION'; end if;

  if ev.capacity is not null and astra_active_count(ev.id) >= ev.capacity then raise exception 'EVENT_FULL'; end if;

  fee := ev.fee_paise;
  approval := coalesce((p->>'manual_approval')::boolean, false);
  minutes := coalesce((p->>'reservation_minutes')::int, 15);
  pst := case when fee > 0 then 'pending' else 'not_required' end;
  st := case when fee > 0 or approval then 'pending' else 'confirmed' end;
  res := case when fee > 0 then now() + make_interval(mins => minutes) end;
  code := 'ASTRA26-' || lpad(nextval('astra_reg_seq')::text, 5, '0');

  insert into registrations(reg_code, event_id, format, team_name, college_name, contingent_code, contact_email,
      contact_phone, extra, status, payment_status, requires_approval, reserved_until, consent, verification_token,
      access_token_hash, idempotency_key)
  values (code, ev.id, fmt, nullif(p->>'team_name',''), p->>'college_name', nullif(p->>'contingent_code',''),
      p->>'contact_email', p->>'contact_phone', coalesce(p->'extra','{}'::jsonb), st, pst, approval, res, true,
      p->>'verification_token', p->>'access_token_hash', p->>'idempotency_key')
  returning id into rid;

  for part in select * from jsonb_array_elements(p->'participants') loop
    insert into participants(registration_id, full_name, email, mobile, department, course_year, roll_number, role)
    values (rid, part->>'full_name', nullif(part->>'email',''), nullif(part->>'mobile',''), part->>'department',
            part->>'course_year', part->>'roll_number', part->>'role');
  end loop;

  select jsonb_agg(jsonb_build_object('id', id, 'name', full_name, 'role', role)) into parts
    from participants where registration_id = rid;
  return jsonb_build_object('id', rid, 'reg_code', code, 'status', st, 'payment_status', pst,
                            'fee_paise', fee, 'participants', parts, 'replayed', false);
end $$;

-- ---------- hold (or re-hold) a seat before creating a Razorpay order ----------
create or replace function astra_reserve_seat(p_reg uuid, p_minutes int) returns jsonb
language plpgsql security definer set search_path = public as $$
declare reg registrations%rowtype; ev events%rowtype;
begin
  select * into reg from registrations where id = p_reg for update;
  if not found then raise exception 'REGISTRATION_NOT_FOUND'; end if;
  if reg.payment_status = 'paid' then raise exception 'ALREADY_PAID'; end if;
  if reg.status in ('rejected') or reg.payment_status = 'refunded' then raise exception 'REGISTRATION_CLOSED'; end if;
  select * into ev from events where id = reg.event_id for update;
  if ev.fee_paise <= 0 then raise exception 'NO_FEE_DUE'; end if;
  if reg.reserved_until is null or reg.reserved_until < now() or reg.status = 'cancelled' then
    if ev.registration_status <> 'open' then raise exception 'EVENT_CLOSED'; end if;
    if ev.capacity is not null and astra_active_count(ev.id) >= ev.capacity then raise exception 'EVENT_FULL'; end if;
  end if;
  update registrations set reserved_until = now() + make_interval(mins => p_minutes),
      status = case when status = 'cancelled' then 'pending' else status end,
      payment_status = case when payment_status in ('cancelled','failed') then 'pending' else payment_status end
    where id = p_reg;
  return jsonb_build_object('fee_paise', ev.fee_paise, 'reg_code', reg.reg_code, 'event_name', ev.name);
end $$;

-- ---------- payment state changes (idempotent) ----------
create or replace function astra_mark_paid(p_order text, p_payment text, p_method text, p_amount int, p_currency text,
                                           p_webhook_event text default null) returns jsonb
language plpgsql security definer set search_path = public as $$
declare pay payments%rowtype; reg registrations%rowtype; ev events%rowtype; review boolean := false;
begin
  select * into pay from payments where razorpay_order_id = p_order for update;
  if not found then raise exception 'PAYMENT_NOT_FOUND'; end if;
  if pay.amount_paise <> p_amount or pay.currency <> p_currency then raise exception 'AMOUNT_MISMATCH'; end if;
  if pay.status = 'paid' then return jsonb_build_object('already', true, 'registration_id', pay.registration_id); end if;
  if pay.status = 'refunded' then return jsonb_build_object('already', true, 'registration_id', pay.registration_id); end if;

  select * into reg from registrations where id = pay.registration_id for update;
  select * into ev from events where id = reg.event_id for update;

  if reg.payment_status = 'paid' then review := true;                       -- a second payment for an already-paid registration
  elsif reg.status = 'cancelled' and ev.capacity is not null and astra_active_count(ev.id) >= ev.capacity then review := true;
  end if;

  update payments set status = 'paid', razorpay_payment_id = p_payment, method = p_method, verified_at = now(),
      last_webhook_event_id = coalesce(p_webhook_event, last_webhook_event_id), failure_reason = null
    where id = pay.id;

  if not review then
    update registrations set payment_status = 'paid',
        status = case when status in ('pending','cancelled') then (case when requires_approval then 'pending' else 'confirmed' end) else status end,
        reserved_until = null
      where id = reg.id;
  else
    update registrations set needs_review = true, payment_status = case when payment_status = 'paid' then 'paid' else payment_status end
      where id = reg.id;
  end if;
  return jsonb_build_object('already', false, 'registration_id', reg.id, 'needs_review', review);
end $$;

create or replace function astra_mark_failed(p_order text, p_payment text, p_reason text) returns void
language plpgsql security definer set search_path = public as $$
declare pay payments%rowtype;
begin
  select * into pay from payments where razorpay_order_id = p_order for update;
  if not found or pay.status in ('paid','refunded') then return; end if;
  update payments set status = 'failed', razorpay_payment_id = coalesce(p_payment, razorpay_payment_id), failure_reason = p_reason where id = pay.id;
  update registrations set payment_status = 'failed' where id = pay.registration_id and payment_status = 'pending';
end $$;

create or replace function astra_mark_refunded(p_payment text) returns void
language plpgsql security definer set search_path = public as $$
declare pay payments%rowtype;
begin
  select * into pay from payments where razorpay_payment_id = p_payment for update;
  if not found then return; end if;
  update payments set status = 'refunded', refund_status = 'processed' where id = pay.id;
  update registrations set payment_status = 'refunded', status = 'cancelled' where id = pay.registration_id;
end $$;

-- ---------- reporting views (read by the Flask admin API only) ----------
create or replace view v_event_stats as
select e.id, e.name, e.category, e.subcategory, e.capacity, e.fee_paise, e.registration_status, e.is_active,
       astra_active_count(e.id) as registered,
       case when e.capacity is null then null else greatest(e.capacity - astra_active_count(e.id), 0) end as seats_left
from events e;

create or replace view v_registrations_admin as
select r.id, r.reg_code, r.event_id, e.name as event_name, e.category, r.format, r.team_name, r.college_name,
       r.contact_email, r.contact_phone, r.status, r.payment_status, r.needs_review, r.created_at,
       (select p.full_name from participants p where p.registration_id = r.id and p.role in ('individual','captain') limit 1) as lead_name,
       (select count(*) from participants p where p.registration_id = r.id)::int as participant_count,
       (select count(*) from registration_documents d where d.registration_id = r.id and d.status = 'pending' and d.deleted_at is null)::int as docs_pending
from registrations r join events e on e.id = r.event_id;

create or replace view v_participants_admin as
select p.id, p.registration_id, r.reg_code, r.event_id, r.status as reg_status, r.payment_status, e.name as event_name, e.category, p.full_name, p.email, p.mobile,
       p.department, p.course_year, p.roll_number, p.role, r.college_name
from participants p join registrations r on r.id = p.registration_id join events e on e.id = r.event_id;

create or replace view v_payments_admin as
select pay.id, pay.registration_id, r.reg_code, e.name as event_name, coalesce(r.team_name,
       (select full_name from participants where registration_id = r.id order by created_at limit 1)) as payer_name,
       pay.amount_paise, pay.currency, pay.status, pay.razorpay_order_id, pay.razorpay_payment_id, pay.method,
       pay.verified_at, pay.refund_status, pay.failure_reason, pay.created_at
from payments pay join registrations r on r.id = pay.registration_id join events e on e.id = r.event_id;

create or replace view v_documents_admin as
select d.id, d.registration_id, r.reg_code, r.event_id, e.name as event_name, d.participant_id, p.full_name as participant_name,
       d.doc_type, d.content_type, d.size_bytes, d.status, d.review_notes, d.uploaded_at
from registration_documents d join registrations r on r.id = d.registration_id join events e on e.id = r.event_id
left join participants p on p.id = d.participant_id where d.deleted_at is null;

create or replace view v_dashboard as
select (select count(*) from events where is_active)::int as total_events,
       (select count(*) from registrations where status <> 'cancelled')::int as total_registrations,
       (select count(*) from registrations where format = 'individual' and status <> 'cancelled')::int as individual_registrations,
       (select count(*) from registrations where format = 'team' and status <> 'cancelled')::int as team_registrations,
       (select count(*) from participants p join registrations r on r.id = p.registration_id where r.status <> 'cancelled')::int as total_participants,
       (select count(*) from registrations where payment_status = 'paid')::int as paid_registrations,
       (select count(*) from registrations where payment_status = 'pending' and status <> 'cancelled')::int as pending_payments,
       (select count(*) from registrations where payment_status = 'failed')::int as failed_payments,
       (select count(distinct registration_id) from registration_documents where status = 'pending' and deleted_at is null)::int as awaiting_documents;

create or replace view v_department_stats as
select coalesce(nullif(p.department, ''), 'Unspecified') as department, count(*)::int as participants
from participants p join registrations r on r.id = p.registration_id where r.status <> 'cancelled'
group by 1 order by 2 desc;

-- ---------- Row Level Security: deny everything to anon / logged-in browser clients ----------
-- The Flask server uses the service_role key, which bypasses RLS. No policies are created on purpose,
-- so the public anon key can read or write nothing.
do $$ declare t text; begin
  foreach t in array array['departments','events','app_settings','registrations','participants','payments',
                           'webhook_events','registration_documents','admin_users','audit_log'] loop
    execute format('alter table %I enable row level security', t);
    execute format('revoke all on %I from anon, authenticated', t);
  end loop; end $$;
revoke all on v_event_stats, v_registrations_admin, v_participants_admin, v_payments_admin, v_documents_admin,
              v_dashboard, v_department_stats from anon, authenticated;
revoke execute on all functions in schema public from anon, authenticated, public;
grant execute on all functions in schema public to service_role;

-- ---------- private Storage bucket (no storage policies = nobody but service_role can touch it) ----------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('astra-documents', 'astra-documents', false, 5242880, array['image/jpeg','image/png','application/pdf'])
on conflict (id) do update set public = false;
