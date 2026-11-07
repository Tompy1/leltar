# leltar – videó generálás (Remotion)

- `npm install`
- `npm run dev` – előnézet (Remotion Studio)
- `npm run render` – MP4 renderelés: `out/video.mp4`
- Tartalom szerkesztése: `src/content.ts`

## Python videógeneráló

Képekből + zenéből videó: lásd `video_generator/README.md`.

## AI képgeneráló

Szövegből kép (OpenAI / Stability AI): lásd `image_generator/README.md`.

## Online AI képgeneráló (Netlify + Supabase)

Bejelentkezéssel védett webes képgeneráló: `web/` (weboldal), `supabase/` (Edge Function + adatbázis).
Telepítés lépésről lépésre: **[DEPLOY.md](DEPLOY.md)**.

## Saját VPS szerveren (ajánlott)

Minden egyben: bejelentkezés, AI képgenerálás, galéria és videókészítés a képekből.
`server/` (Flask alkalmazás), `docker-compose.yml` (alkalmazás + Caddy HTTPS).

```
cp .env.example .env && nano .env
docker compose up -d --build
docker compose exec app flask --app app add-user te@pelda.hu
```

Lépésről lépésre: **VPS_telepitesi_utmutato.pdf**.
