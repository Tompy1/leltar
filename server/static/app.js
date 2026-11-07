(() => {
  const $ = (id) => document.getElementById(id);
  const state = JSON.parse($("initial-data").textContent);
  const selected = []; // kijelölt kép-azonosítók, kijelölési sorrendben

  function setMsg(el, text, isError = false) {
    el.textContent = text;
    el.classList.toggle("error", isError);
  }

  async function api(url, options = {}) {
    const resp = await fetch(url, {
      method: "POST",
      ...options,
      headers: { "X-Requested-With": "fetch", ...(options.headers || {}) },
    });
    if (resp.status === 401) {
      location.href = "/login";
      throw new Error("Jelentkezz be újra.");
    }
    let data = {};
    try { data = await resp.json(); } catch { /* nem JSON */ }
    if (!resp.ok) {
      const fallback = resp.status === 413 ? "A feltöltött fájl túl nagy (max. 40 MB)."
        : resp.status === 502 || resp.status === 504 ? "A szerver nem válaszolt időben. Próbáld újra."
        : `Hiba történt (${resp.status}).`;
      throw new Error(data.error || fallback);
    }
    return data;
  }

  // ---------- Képgenerálás ----------
  $("gen-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("gen-btn");
    btn.disabled = true;
    setMsg($("gen-msg"), "Generálás folyamatban... ez akár egy percig is tarthat.");
    try {
      const data = await api("/api/generate", {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: $("prompt").value,
          ratio: $("ratio").value,
          negative: $("negative").value || null,
        }),
      });
      state.images.unshift(data.image);
      $("result-img").src = data.image.url;
      $("result-img").alt = data.image.prompt;
      $("result-caption").textContent = `Kész (${data.provider}).`;
      $("result-download").href = data.image.url + "?download=1";
      $("result").hidden = false;
      $("remaining").textContent = data.remaining;
      setMsg($("gen-msg"), "");
      renderGallery();
    } catch (err) {
      setMsg($("gen-msg"), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- Galéria ----------
  function renderGallery() {
    const gallery = $("gallery");
    gallery.replaceChildren();
    $("gallery-empty").hidden = state.images.length > 0;
    for (const img of state.images) {
      const fig = document.createElement("figure");
      const order = selected.indexOf(img.id);
      fig.classList.toggle("selected", order >= 0);

      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "pick";
      pick.setAttribute("aria-pressed", order >= 0);
      pick.title = order >= 0 ? "Kijelölés megszüntetése" : "Kijelölés videóhoz";
      const el = document.createElement("img");
      el.src = img.url;
      el.alt = img.prompt;
      el.loading = "lazy";
      pick.append(el);
      if (order >= 0) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = order + 1;
        pick.append(badge);
      }
      pick.addEventListener("click", () => toggleSelect(img.id));

      const cap = document.createElement("figcaption");
      cap.textContent = img.prompt;
      cap.title = img.prompt;

      const actions = document.createElement("div");
      actions.className = "actions";
      const dl = document.createElement("a");
      dl.href = img.url + "?download=1";
      dl.textContent = "Letöltés";
      const del = document.createElement("button");
      del.type = "button";
      del.className = "link danger";
      del.textContent = "Törlés";
      del.addEventListener("click", () => deleteImage(img));
      actions.append(dl, del);

      fig.append(pick, cap, actions);
      gallery.append(fig);
    }
    $("sel-count").textContent = selected.length;
  }

  function toggleSelect(id) {
    const i = selected.indexOf(id);
    if (i >= 0) selected.splice(i, 1);
    else if (selected.length >= state.maxVideoImages) {
      setMsg($("video-msg"), `Legfeljebb ${state.maxVideoImages} kép jelölhető ki.`, true);
      return;
    } else selected.push(id);
    renderGallery();
  }

  $("sel-clear").addEventListener("click", () => {
    selected.length = 0;
    renderGallery();
  });

  async function deleteImage(img) {
    if (!confirm("Biztosan törlöd ezt a képet?")) return;
    try {
      await api(`/api/images/${img.id}/delete`);
      state.images = state.images.filter((x) => x.id !== img.id);
      const i = selected.indexOf(img.id);
      if (i >= 0) selected.splice(i, 1);
      renderGallery();
    } catch (err) {
      alert(err.message);
    }
  }

  // ---------- Videó ----------
  $("video-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selected.length) {
      setMsg($("video-msg"), "Előbb jelölj ki képeket a galériában.", true);
      return;
    }
    const form = new FormData();
    form.append("image_ids", selected.join(","));
    form.append("seconds", $("seconds").value);
    form.append("crossfade", $("crossfade").value);
    form.append("size", $("size").value);
    form.append("orientation", $("orientation").value);
    form.append("captions", $("captions").checked ? "1" : "0");
    if ($("music").files[0]) form.append("music", $("music").files[0]);

    const btn = $("video-btn");
    btn.disabled = true;
    setMsg($("video-msg"), `Videó készítése ${selected.length} képből... ez néhány percig is eltarthat, ne zárd be az oldalt.`);
    try {
      const data = await api("/api/video", { body: form });
      state.videos.unshift(data.video);
      renderVideos();
      setMsg($("video-msg"), "Kész! Lent, a „Videóim” között találod.");
    } catch (err) {
      setMsg($("video-msg"), err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  function renderVideos() {
    const list = $("videos");
    list.replaceChildren();
    $("videos-empty").hidden = state.videos.length > 0;
    for (const v of state.videos) {
      const fig = document.createElement("figure");
      const video = document.createElement("video");
      video.src = v.url;
      video.controls = true;
      video.preload = "metadata";
      const cap = document.createElement("figcaption");
      cap.textContent = `${v.created} · ${v.image_count} kép`;
      const actions = document.createElement("div");
      actions.className = "actions";
      const dl = document.createElement("a");
      dl.href = v.url + "?download=1";
      dl.textContent = "Letöltés";
      const del = document.createElement("button");
      del.type = "button";
      del.className = "link danger";
      del.textContent = "Törlés";
      del.addEventListener("click", async () => {
        if (!confirm("Biztosan törlöd ezt a videót?")) return;
        try {
          await api(`/api/videos/${v.id}/delete`);
          state.videos = state.videos.filter((x) => x.id !== v.id);
          renderVideos();
        } catch (err) {
          alert(err.message);
        }
      });
      actions.append(dl, del);
      fig.append(video, cap, actions);
      list.append(fig);
    }
  }

  renderGallery();
  renderVideos();
})();
