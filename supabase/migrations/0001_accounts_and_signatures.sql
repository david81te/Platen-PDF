-- Platen PDF: accounts and synced signatures.
--
-- The account is optional. Everything in the desktop application works signed
-- out, with signatures held on the local machine; signing in exists so the
-- same signatures appear on the phone. Documents are never uploaded - only
-- the signature images.
--
-- Row-level security is the entire security model here, so every table and
-- the storage bucket deny by default and grant only to the owning user.

-- ---------------------------------------------------------------- profiles
create table if not exists public.profiles (
  id                    uuid primary key references auth.users (id) on delete cascade,
  email                 text        not null,
  display_name          text,
  -- Donation reminders are a separate, explicit opt-in. Creating an account
  -- is not consent to be emailed marketing; CAN-SPAM and GDPR both care.
  reminders_opted_in    boolean     not null default false,
  reminders_opted_in_at timestamptz,
  last_reminded_at      timestamptz,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- -------------------------------------------------------------- signatures
-- The PNG lives in storage at signatures/<user id>/<signature id>.png; this
-- table is the metadata and the sync record.
create table if not exists public.signatures (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid        not null references auth.users (id) on delete cascade,
  name         text        not null,
  role         text,
  storage_path text        not null,
  width        integer,
  height       integer,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  -- Soft delete: a device that has been offline needs to learn that a
  -- signature was removed elsewhere, which a hard delete cannot tell it.
  deleted_at   timestamptz
);

create index if not exists signatures_user_live_idx
  on public.signatures (user_id, updated_at desc)
  where deleted_at is null;

-- ----------------------------------------------------------------- triggers
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $fn$
begin
  new.updated_at = now();
  return new;
end;
$fn$;

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

drop trigger if exists signatures_touch on public.signatures;
create trigger signatures_touch before update on public.signatures
  for each row execute function public.touch_updated_at();

-- A profile row the moment an account exists, so the rest of the app can
-- assume one is always there.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = '' as $fn$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$fn$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ------------------------------------------------------------------- RLS
alter table public.profiles   enable row level security;
alter table public.signatures enable row level security;

drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select using ((select auth.uid()) = id);

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update using ((select auth.uid()) = id)
          with check ((select auth.uid()) = id);

-- No insert or delete policy on purpose: the trigger creates the row, and the
-- cascade from auth.users removes it. Nothing else should be writing here.

drop policy if exists signatures_select_own on public.signatures;
create policy signatures_select_own on public.signatures
  for select using ((select auth.uid()) = user_id);

drop policy if exists signatures_insert_own on public.signatures;
create policy signatures_insert_own on public.signatures
  for insert with check ((select auth.uid()) = user_id);

drop policy if exists signatures_update_own on public.signatures;
create policy signatures_update_own on public.signatures
  for update using ((select auth.uid()) = user_id)
          with check ((select auth.uid()) = user_id);

drop policy if exists signatures_delete_own on public.signatures;
create policy signatures_delete_own on public.signatures
  for delete using ((select auth.uid()) = user_id);

-- --------------------------------------------------------------- storage
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('signatures', 'signatures', false, 5242880, array['image/png'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

-- The first path segment is the owner's id, which is what these policies key
-- on. A file at signatures/<someone else>/x.png is unreachable.
drop policy if exists signature_files_select_own on storage.objects;
create policy signature_files_select_own on storage.objects
  for select using (
    bucket_id = 'signatures'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists signature_files_insert_own on storage.objects;
create policy signature_files_insert_own on storage.objects
  for insert with check (
    bucket_id = 'signatures'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists signature_files_update_own on storage.objects;
create policy signature_files_update_own on storage.objects
  for update using (
    bucket_id = 'signatures'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists signature_files_delete_own on storage.objects;
create policy signature_files_delete_own on storage.objects
  for delete using (
    bucket_id = 'signatures'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
