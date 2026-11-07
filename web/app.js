import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/+esm";

const BUCKET = "generated-images";
const downloadUrl = (url) => url + (url.includes("?") ? "&" : "?") + "download=";
const $ = (id) => document.getElementById(id);
const cfg = window.APP_CONFIG || {};

if (!cfg.SUPABASE_URL || !cfg.SUPABASE_ANON_KEY) {
  $("config-error").hidden = false;
  throw new Error("Hiányzó Supabase beállítás");
}

const supabase = createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY);

function setMsg(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("error", isError);
}

// ---------- Bejelentkezés ----------

function showView(session) {
  const loggedIn = !!session;
  $("login-view").hidden = loggedIn;
  $("app-view").hidden = !loggedIn;
  $("user-bar").hidden = !loggedIn;
  if (loggedIn) {
    $("user-email").textContent = session.user.email;
    loadGallery();
  }
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = $("email").value.trim();
  const password = $("password").value;
  if (!password) {
    setMsg($("login-msg"), "Add meg a jelszót, vagy kérj belépési linket.", true);
    return;
  }
  setMsg($("login-msg"), "Belépés...");
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  setMsg($("login-msg"), error ? "Hibás e-mail vagy jelszó." : "", !!error);
});

$("magic-link").addEventListener("click", async () => {
  const email = $("email").value.trim();
  if (!email) {
    setMsg($("login-msg"), "Előbb add meg az e-mail címed.", true);
    return;
  }
  setMsg($("login-msg"), "Küldés...");
  const { error } = await supabase.auth.signInWithOtp({
    email,
    // Új fiókot NEM hoz létre: csak a már felvett felhasználók léphetnek be.
    options: { shouldCreateUser: false, emailRedirectTo: location.origin + location.pathname },
  });
  setMsg(
    $("login-msg"),
    error ? "Ehhez az e-mail címhez nincs fiók, vagy túl sok kérés történt." : "Elküldtük a belépési linket – nézd meg a postafiókod.",
    !!error,
  );
});

$("logout").addEventListener("click", () => supabase.auth.signOut());

supabase.auth.onAuthStateChange((_event, session) => showView(session));

// ---------- Generálás ----------

$("gen-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("gen-btn");
  btn.disabled = true;
  setMsg($("gen-msg"), "Generálás folyamatban... ez akár egy percig is tarthat.");
  try {
    const { data, error } = await supabase.functions.invoke("generate-image", {
      body: {
        prompt: $("prompt").value,
        ratio: $("ratio").value,
        negative: $("negative").value || undefined,
      },
    });
    if (error) {
      let msg = error.message;
      try { msg = (await error.context.json()).error || msg; } catch { /* nincs JSON */ }
      throw new Error(msg);
    }
    $("result-img").src = data.url;
    $("result-img").alt = $("prompt").value;
    $("result-caption").textContent = `Kész (${data.provider}). Ma még ${data.remaining} kép generálható.`;
    $("result-download").href = downloadUrl(data.url);
    $("result").hidden = false;
    setMsg($("gen-msg"), "");
    loadGallery();
  } catch (err) {
    setMsg($("gen-msg"), err.message || "Hiba történt.", true);
  } finally {
    btn.disabled = false;
  }
});

// ---------- Galéria ----------

async function loadGallery() {
  const { data: rows, error } = await supabase
    .from("generations")
    .select("id, prompt, path, created_at")
    .order("created_at", { ascending: false })
    .limit(48);
  const gallery = $("gallery");
  gallery.replaceChildren();
  if (error) {
    gallery.textContent = "A galéria nem tölthető be.";
    return;
  }
  $("gallery-empty").hidden = rows.length > 0;
  if (!rows.length) return;

  const { data: urls } = await supabase.storage.from(BUCKET)
    .createSignedUrls(rows.map((r) => r.path), 3600);
  const urlByPath = Object.fromEntries((urls || []).map((u) => [u.path, u.signedUrl]));

  for (const row of rows) {
    const url = urlByPath[row.path];
    if (!url) continue;
    const fig = document.createElement("figure");
    const img = document.createElement("img");
    img.src = url;
    img.alt = row.prompt;
    img.loading = "lazy";
    const cap = document.createElement("figcaption");
    cap.textContent = row.prompt;
    cap.title = row.prompt;
    const actions = document.createElement("div");
    actions.className = "actions";
    const dl = document.createElement("a");
    dl.href = downloadUrl(url);
    dl.textContent = "Letöltés";
    const del = document.createElement("button");
    del.className = "link";
    del.textContent = "Törlés";
    del.addEventListener("click", () => deleteImage(row));
    actions.append(dl, del);
    fig.append(img, cap, actions);
    gallery.append(fig);
  }
}

async function deleteImage(row) {
  if (!confirm("Biztosan törlöd ezt a képet?")) return;
  await supabase.storage.from(BUCKET).remove([row.path]);
  await supabase.from("generations").delete().eq("id", row.id);
  loadGallery();
}
