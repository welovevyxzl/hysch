-- Hysch: adminplan med obegränsade samtal, och en lista över e-postadresser som alltid får den.
-- Planen väljs av servern när kontot skapas (och här för de konton som redan finns). Appen kan fortfarande aldrig skriva i tabellerna.

-- 1) Planen "admin" får finnas, och molnsamtal kan vara obegränsade (null = obegränsat, precis som för lokala samtal).
do $$
declare c text;
begin
  for c in
    select conname from pg_constraint
    where conrelid = 'public.plan_limits'::regclass and contype = 'c' and pg_get_constraintdef(oid) like '%founding%'
  loop
    execute format('alter table public.plan_limits drop constraint %I', c);
  end loop;
end $$;
alter table public.plan_limits add constraint plan_limits_plan_check check (plan in ('free', 'founding', 'pro', 'admin'));
alter table public.plan_limits alter column cloud_calls_per_month drop not null;

insert into public.plan_limits (plan, cloud_calls_per_month, local_calls_per_month, max_call_minutes)
values ('admin', null, null, 240)
on conflict (plan) do nothing;

-- 2) Vilka e-postadresser är admin. RLS på och inga rättigheter för appen: bara servern kommer åt tabellen.
create table if not exists public.admin_emails (
  email text primary key check (email = lower(email))
);
alter table public.admin_emails enable row level security;
revoke all on public.admin_emails from anon, authenticated;

insert into public.admin_emails (email) values ('adiankarolhipsz@gmail.com') on conflict do nothing;

-- 3) Nya konton med en admin-adress får adminplanen direkt. (Inloggning sker med en kod som mejlas till adressen,
--    så bara den som kommer åt brevlådan kan använda kontot.)
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.subscriptions (user_id, plan)
  values (
    new.id,
    case when exists (select 1 from public.admin_emails a where a.email = lower(new.email)) then 'admin' else 'free' end
  )
  on conflict do nothing;
  return new;
end $$;

-- 4) Konton som redan finns.
update public.subscriptions s
set plan = 'admin', status = 'active', cancel_at_period_end = false, updated_at = now()
from auth.users u
where u.id = s.user_id and lower(u.email) in (select email from public.admin_emails);
