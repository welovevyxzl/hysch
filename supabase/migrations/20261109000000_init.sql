-- Hysch: konton, planer och samtalsgränser.
-- Regel nr 1: RLS är PÅ i varje tabell, och appen får bara LÄSA sina egna rader.
-- Allt som kostar pengar (planbyte, räkning av samtal) görs av servern, aldrig av appen.

-- ---------------------------------------------------------------- planer och gränser
create table public.plan_limits (
  plan                 text primary key check (plan in ('free', 'founding', 'pro')),
  cloud_calls_per_month integer not null check (cloud_calls_per_month >= 0),
  local_calls_per_month integer check (local_calls_per_month >= 0),   -- null = obegränsat
  max_call_minutes     integer not null check (max_call_minutes > 0)
);

insert into public.plan_limits (plan, cloud_calls_per_month, local_calls_per_month, max_call_minutes) values
  ('free',     0,  3,    30),
  ('founding', 10, null, 30),
  ('pro',      20, null, 30);

-- ---------------------------------------------------------------- prenumeration (en rad per användare)
create table public.subscriptions (
  user_id                uuid primary key references auth.users (id) on delete cascade,
  plan                   text not null default 'free' references public.plan_limits (plan),
  status                 text not null default 'active'
                         check (status in ('active', 'trialing', 'past_due', 'canceled')),
  stripe_customer_id     text unique,
  stripe_subscription_id text unique,
  current_period_start   timestamptz,
  current_period_end     timestamptz,
  cancel_at_period_end   boolean not null default false,
  updated_at             timestamptz not null default now()
);

-- ---------------------------------------------------------------- samtal som räknas mot gränsen
create table public.call_usage (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  mode       text not null check (mode in ('cloud', 'local')),
  started_at timestamptz not null default now(),
  ended_at   timestamptz
);
create index call_usage_user_started on public.call_usage (user_id, started_at desc);

-- ---------------------------------------------------------------- RLS: på överallt
alter table public.plan_limits   enable row level security;
alter table public.subscriptions enable row level security;
alter table public.call_usage    enable row level security;

-- Läsa: bara sin egen rad. Gränserna är inte hemliga och får läsas av inloggade.
create policy "egen prenumeration" on public.subscriptions
  for select to authenticated using (user_id = (select auth.uid()));
create policy "eget samtalsanvändande" on public.call_usage
  for select to authenticated using (user_id = (select auth.uid()));
create policy "gränser för inloggade" on public.plan_limits
  for select to authenticated using (true);
-- Inga policies för insert, update eller delete: appen kan alltså aldrig skriva i tabellerna.

-- Dubbel säkerhet: ta bort alla rättigheter först, ge sedan tillbaka bara läsning.
revoke all on public.plan_limits, public.subscriptions, public.call_usage from anon, authenticated;
grant select on public.plan_limits, public.subscriptions, public.call_usage to authenticated;

-- ---------------------------------------------------------------- ny användare får planen "free"
create function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.subscriptions (user_id) values (new.id) on conflict do nothing;
  return new;
end $$;
revoke all on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------- "Mitt konto": plan, gränser och förbrukning
-- security_invoker = vyn följer samma RLS som den som frågar, så ingen ser någon annans rad.
create view public.my_account with (security_invoker = true) as
select
  s.user_id,
  s.plan,
  s.status,
  s.cancel_at_period_end,
  s.current_period_end,
  l.cloud_calls_per_month,
  l.local_calls_per_month,
  l.max_call_minutes,
  coalesce(s.current_period_start, date_trunc('month', now())) as period_start,
  (select count(*) from public.call_usage u
    where u.user_id = s.user_id and u.mode = 'cloud'
      and u.started_at >= coalesce(s.current_period_start, date_trunc('month', now()))) as cloud_calls_used,
  (select count(*) from public.call_usage u
    where u.user_id = s.user_id and u.mode = 'local'
      and u.started_at >= coalesce(s.current_period_start, date_trunc('month', now()))) as local_calls_used
from public.subscriptions s
join public.plan_limits l on l.plan = s.plan;

revoke all on public.my_account from anon, authenticated;
grant select on public.my_account to authenticated;

-- ---------------------------------------------------------------- starta samtal: servern avgör, atomiskt
-- Användaren kommer från inloggningen (auth.uid()), aldrig från ett argument, så ingen kan räkna åt någon annan.
create function public.start_call(p_mode text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  uid     uuid := auth.uid();
  acct    record;
  used    bigint;
  cap     integer;
  new_id  uuid;
begin
  if uid is null then
    return jsonb_build_object('ok', false, 'reason', 'not_logged_in');
  end if;
  if p_mode not in ('cloud', 'local') then
    return jsonb_build_object('ok', false, 'reason', 'bad_mode');
  end if;

  perform pg_advisory_xact_lock(hashtextextended(uid::text, 0));   -- två samtidiga starter kan inte smita förbi gränsen

  select * into acct from public.my_account_for(uid);
  if acct.status in ('canceled') and acct.plan <> 'free' then
    return jsonb_build_object('ok', false, 'reason', 'subscription_inactive');
  end if;

  if p_mode = 'cloud' then
    cap := acct.cloud_calls_per_month; used := acct.cloud_calls_used;
  else
    cap := acct.local_calls_per_month; used := acct.local_calls_used;
  end if;

  if cap is not null and used >= cap then
    return jsonb_build_object('ok', false, 'reason', 'limit_reached', 'used', used, 'limit', cap);
  end if;

  insert into public.call_usage (user_id, mode) values (uid, p_mode) returning id into new_id;
  return jsonb_build_object('ok', true, 'call_id', new_id, 'max_minutes', acct.max_call_minutes,
                            'used', used + 1, 'limit', cap);
end $$;

-- Hjälpfunktion som räknar åt en given användare (används bara inifrån start_call).
create function public.my_account_for(p_uid uuid)
returns table (plan text, status text, cloud_calls_per_month integer, local_calls_per_month integer,
               max_call_minutes integer, cloud_calls_used bigint, local_calls_used bigint)
language sql security definer set search_path = '' as $$
  select s.plan, s.status, l.cloud_calls_per_month, l.local_calls_per_month, l.max_call_minutes,
    (select count(*) from public.call_usage u where u.user_id = s.user_id and u.mode = 'cloud'
       and u.started_at >= coalesce(s.current_period_start, date_trunc('month', now()))),
    (select count(*) from public.call_usage u where u.user_id = s.user_id and u.mode = 'local'
       and u.started_at >= coalesce(s.current_period_start, date_trunc('month', now())))
  from public.subscriptions s
  join public.plan_limits l on l.plan = s.plan
  where s.user_id = p_uid;
$$;
revoke all on function public.my_account_for(uuid) from public, anon, authenticated;

-- Appen får anropa start_call, men inget annat.
revoke all on function public.start_call(text) from public, anon;
grant execute on function public.start_call(text) to authenticated;

-- ---------------------------------------------------------------- avsluta samtal
create function public.end_call(p_call_id uuid) returns void
language sql security definer set search_path = '' as $$
  update public.call_usage set ended_at = now()
  where id = p_call_id and user_id = auth.uid() and ended_at is null;
$$;
revoke all on function public.end_call(uuid) from public, anon;
grant execute on function public.end_call(uuid) to authenticated;
