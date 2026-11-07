# AI Képgeneráló online – Netlify + Supabase

```
Böngésző (web/)  ──►  Supabase Edge Function (generate-image)  ──►  OpenAI / Stability AI
   Netlify               itt van a titkos API-kulcs
                         képek: Supabase Storage, napló: generations tábla
```

- **Bejelentkezés kötelező.** Fiókot csak te hozhatsz létre (a nyilvános regisztrációt kikapcsoljuk).
- A titkos API-kulcs **csak a Supabase-ben** van, a böngészőbe és a GitHubra soha nem kerül.
- Napi korlát felhasználónként (alapból 20 kép / 24 óra).
- Minden felhasználó csak a saját képeit látja.

---

## 1. Supabase projekt

1. Regisztrálj: <https://supabase.com> → **New project** (régió: pl. Frankfurt). Jegyezd fel az adatbázis-jelszót.
2. **Project Settings → API** (vagy **API Keys**) oldalon jegyezd fel:
   - **Project URL** – pl. `https://abcdxyz.supabase.co`
   - **anon / publishable** kulcs (ez nyilvános, a weboldalba kerül)
   - a **Project ref** – az URL első része (`abcdxyz`)

   ⚠️ A `service_role` / `secret` kulcsot **sehova ne másold be**.

## 2. Adatbázis és tárhely létrehozása

Supabase felületen: **SQL Editor → New query**, másold be a
`supabase/migrations/20261107000000_image_generator.sql` tartalmát, majd **Run**.

(Vagy parancssorból, a 3. lépés után: `npx supabase db push`.)

## 3. Edge Function feltöltése

Kell hozzá a Node.js (<https://nodejs.org>). A repó gyökérmappájában:

```bash
npx supabase login
npx supabase link --project-ref abcdxyz          # a saját Project ref-ed

# API-kulcs titokként – az ÚJ kulcsodat írd be (a chatben megosztottat töröld!)
npx supabase secrets set OPENAI_API_KEY=sk-...
#   vagy ha Stability AI kulcsod van:
# npx supabase secrets set STABILITY_API_KEY=sk-...

# opcionális beállítások
npx supabase secrets set DAILY_LIMIT=20
npx supabase secrets set ALLOWED_EMAILS=te@pelda.hu,masik@pelda.hu

npx supabase functions deploy generate-image
```

> Nem tudod, melyik szolgáltató kulcsa? Állítsd be `OPENAI_API_KEY`-ként; ha generáláskor
> „Az API-kulcs hibás, vagy nem ehhez a szolgáltatóhoz tartozik” üzenetet kapsz:
> `npx supabase secrets unset OPENAI_API_KEY` és `npx supabase secrets set STABILITY_API_KEY=sk-...`.

## 4. Bejelentkezés beállítása (Supabase → Authentication)

1. **Sign In / Providers** (vagy **Providers → Email**): az **Email** legyen bekapcsolva,
   és kapcsold **KI** az **Allow new users to sign up** opciót → így idegen nem tud regisztrálni.
2. **Users → Add user → Create new user**: add meg a saját e-mail címed és egy jelszót,
   pipáld be az **Auto Confirm User** opciót. Ugyanígy vehetsz fel másokat is.
3. **URL Configuration** (az 5. lépés után, amikor már megvan a Netlify cím):
   - **Site URL**: `https://a-te-oldalad.netlify.app`
   - **Redirect URLs**: ugyanez (a „belépési link e-mailben” funkcióhoz kell).

## 5. Weboldal a Netlify-on

1. <https://app.netlify.com> → **Add new site → Import an existing project → GitHub** →
   válaszd a `leltar` repót és azt az ágat, amin a kód van.
2. A build beállításokat a `netlify.toml` megadja (publish: `web`), nem kell módosítani.
3. **Site configuration → Environment variables** – adj hozzá kettőt:
   - `SUPABASE_URL` = a Project URL (pl. `https://abcdxyz.supabase.co`)
   - `SUPABASE_ANON_KEY` = az **anon / publishable** kulcs
4. **Deploys → Trigger deploy**. Kész: nyisd meg az oldal címét, és jelentkezz be.

Ezután minden GitHubra feltöltött módosítás után a Netlify magától frissíti az oldalt.
Az Edge Function módosítása után újra kell futtatni: `npx supabase functions deploy generate-image`.

---

## Hibaelhárítás

| Jelenség | Megoldás |
|---|---|
| „Hiányzik a beállítás…” az oldalon | A Netlify környezeti változók hiányoznak → 5/3. lépés, majd új deploy. |
| „Hibás e-mail vagy jelszó.” | Supabase → Authentication → Users: létezik és megerősített a felhasználó? |
| „Jelentkezz be a képgeneráláshoz.” | Lejárt munkamenet: jelentkezz ki és be. |
| „Az API-kulcs hibás…” | Rossz szolgáltató vagy visszavont kulcs → 3. lépés vége. |
| „Váratlan szerverhiba…” | Supabase → Edge Functions → generate-image → **Logs**. Gyakran a 2. lépés (SQL) maradt ki. |
| A galéria üres marad | A 2. lépés SQL-je nem futott le. |

## Helyi kipróbálás (opcionális)

```bash
cp web/config.example.js web/config.js   # töltsd ki az URL-lel és az anon kulccsal
python3 -m http.server 8000 --directory web
# böngésző: http://localhost:8000  (a localhost-ot is add hozzá a Redirect URLs-hez)
```
