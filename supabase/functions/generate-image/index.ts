// Supabase Edge Function: szövegből AI-kép generálása bejelentkezett felhasználóknak.
//
// Titkok (supabase secrets set ...):
//   OPENAI_API_KEY      – ha OpenAI kulcsod van
//   STABILITY_API_KEY   – ha Stability AI kulcsod van
//   IMAGE_PROVIDER      – opcionális: "openai" | "stability" (alap: ami be van állítva)
//   DAILY_LIMIT         – opcionális: napi képkorlát felhasználónként (alap: 20)
//   ALLOWED_EMAILS      – opcionális: vesszővel elválasztott e-mail lista, csak ők használhatják
//
// A SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY automatikusan elérhető.

import { createClient } from "jsr:@supabase/supabase-js@2";

const BUCKET = "generated-images";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const OPENAI_SIZES: Record<string, string> = {
  "1:1": "1024x1024",
  "16:9": "1536x1024",
  "3:2": "1536x1024",
  "9:16": "1024x1536",
  "2:3": "1024x1536",
};
const STABILITY_RATIOS = new Set(["1:1", "16:9", "9:16", "3:2", "2:3"]);

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

async function apiError(resp: Response, provider: string): Promise<HttpError> {
  let msg = await resp.text();
  try {
    const data = JSON.parse(msg);
    msg = data?.error?.message ?? data?.errors?.join?.("; ") ?? data?.message ?? msg;
  } catch { /* nem JSON */ }
  console.error(`${provider} hiba ${resp.status}: ${msg}`);
  const hints: Record<number, string> = {
    401: "Az API-kulcs hibás, vagy nem ehhez a szolgáltatóhoz tartozik.",
    402: "Elfogyott a kredit a szolgáltatónál.",
    429: "Túl sok kérés vagy elfogyott a keret a szolgáltatónál.",
  };
  return new HttpError(502, hints[resp.status] ?? `A(z) ${provider} hibát jelzett: ${msg}`);
}

async function generateOpenAI(prompt: string, ratio: string, key: string): Promise<Uint8Array> {
  const resp = await fetch("https://api.openai.com/v1/images/generations", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "gpt-image-1",
      prompt,
      size: OPENAI_SIZES[ratio] ?? "1024x1024",
      n: 1,
    }),
  });
  if (!resp.ok) throw await apiError(resp, "OpenAI");
  const data = await resp.json();
  const item = data.data[0];
  if (item.b64_json) return Uint8Array.from(atob(item.b64_json), (c) => c.charCodeAt(0));
  const img = await fetch(item.url);
  return new Uint8Array(await img.arrayBuffer());
}

async function generateStability(
  prompt: string,
  ratio: string,
  key: string,
  negative?: string,
): Promise<Uint8Array> {
  const form = new FormData();
  form.append("prompt", prompt);
  form.append("output_format", "png");
  form.append("aspect_ratio", STABILITY_RATIOS.has(ratio) ? ratio : "1:1");
  if (negative) form.append("negative_prompt", negative);
  const resp = await fetch("https://api.stability.ai/v2beta/stable-image/generate/core", {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, Accept: "image/*" },
    body: form,
  });
  if (!resp.ok) throw await apiError(resp, "Stability AI");
  return new Uint8Array(await resp.arrayBuffer());
}

function pickProvider(): { provider: "openai" | "stability"; key: string } {
  const openai = Deno.env.get("OPENAI_API_KEY");
  const stability = Deno.env.get("STABILITY_API_KEY");
  const wanted = Deno.env.get("IMAGE_PROVIDER")?.toLowerCase();
  if (wanted === "stability" && stability) return { provider: "stability", key: stability };
  if (wanted === "openai" && openai) return { provider: "openai", key: openai };
  if (openai) return { provider: "openai", key: openai };
  if (stability) return { provider: "stability", key: stability };
  throw new HttpError(500, "Nincs beállítva API-kulcs (OPENAI_API_KEY vagy STABILITY_API_KEY).");
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  if (req.method !== "POST") return json({ error: "Csak POST kérés engedélyezett." }, 405);

  try {
    // 1. Bejelentkezés ellenőrzése
    const authHeader = req.headers.get("Authorization") ?? "";
    const url = Deno.env.get("SUPABASE_URL")!;
    const userClient = createClient(url, Deno.env.get("SUPABASE_ANON_KEY")!, {
      global: { headers: { Authorization: authHeader } },
    });
    const { data: { user }, error: authError } = await userClient.auth.getUser();
    if (authError || !user) throw new HttpError(401, "Jelentkezz be a képgeneráláshoz.");

    const allowed = (Deno.env.get("ALLOWED_EMAILS") ?? "")
      .split(",").map((e) => e.trim().toLowerCase()).filter(Boolean);
    if (allowed.length && !allowed.includes((user.email ?? "").toLowerCase())) {
      throw new HttpError(403, "Ehhez a fiókhoz nincs jogosultság.");
    }

    // 2. Bemenet
    const body = await req.json().catch(() => ({}));
    const prompt = String(body.prompt ?? "").trim();
    const ratio = String(body.ratio ?? "1:1");
    const negative = body.negative ? String(body.negative).slice(0, 1000) : undefined;
    if (!prompt) throw new HttpError(400, "Add meg, milyen képet szeretnél.");
    if (prompt.length > 2000) throw new HttpError(400, "A leírás legfeljebb 2000 karakter lehet.");

    // 3. Napi korlát
    const admin = createClient(url, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
    const limit = Number(Deno.env.get("DAILY_LIMIT") ?? "20");
    const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
    const { count, error: countError } = await admin
      .from("generations")
      .select("id", { count: "exact", head: true })
      .eq("user_id", user.id)
      .gte("created_at", since);
    if (countError) throw countError;
    if ((count ?? 0) >= limit) {
      throw new HttpError(429, `Elérted a napi korlátot (${limit} kép / 24 óra).`);
    }

    // 4. Generálás
    const { provider, key } = pickProvider();
    const image = provider === "openai"
      ? await generateOpenAI(prompt, ratio, key)
      : await generateStability(prompt, ratio, key, negative);

    // 5. Mentés a tárhelyre és naplózás
    const path = `${user.id}/${Date.now()}-${crypto.randomUUID().slice(0, 8)}.png`;
    const { error: upError } = await admin.storage.from(BUCKET)
      .upload(path, image, { contentType: "image/png" });
    if (upError) throw upError;
    const { error: insError } = await admin.from("generations")
      .insert({ user_id: user.id, prompt, provider, ratio, path });
    if (insError) throw insError;

    const { data: signed, error: signError } = await admin.storage.from(BUCKET)
      .createSignedUrl(path, 3600);
    if (signError) throw signError;

    return json({ path, url: signed.signedUrl, provider, remaining: limit - (count ?? 0) - 1 });
  } catch (err) {
    if (err instanceof HttpError) return json({ error: err.message }, err.status);
    console.error(err);
    return json({ error: "Váratlan szerverhiba. Részletek a Supabase naplóban." }, 500);
  }
});
