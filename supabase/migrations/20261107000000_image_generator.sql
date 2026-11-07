-- AI képgeneráló: naplótábla + privát tárhely a képeknek.

create table if not exists public.generations (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users (id) on delete cascade,
  prompt text not null,
  provider text not null,
  ratio text not null default '1:1',
  path text not null,
  created_at timestamptz not null default now()
);

create index if not exists generations_user_created_idx
  on public.generations (user_id, created_at desc);

alter table public.generations enable row level security;

-- A felhasználó csak a saját generálásait látja és törölheti.
-- Beszúrni csak az Edge Function tud (service role), így a napi korlát nem kerülhető meg.
create policy "sajat generalasok olvasasa" on public.generations
  for select to authenticated using (user_id = auth.uid());
create policy "sajat generalasok torlese" on public.generations
  for delete to authenticated using (user_id = auth.uid());

-- Privát tárhely: a képek a <user_id>/ mappába kerülnek.
insert into storage.buckets (id, name, public)
values ('generated-images', 'generated-images', false)
on conflict (id) do nothing;

create policy "sajat kepek olvasasa" on storage.objects
  for select to authenticated
  using (bucket_id = 'generated-images' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "sajat kepek torlese" on storage.objects
  for delete to authenticated
  using (bucket_id = 'generated-images' and (storage.foldername(name))[1] = auth.uid()::text);
