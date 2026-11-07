"""
AI képgeneráló: szöveges leírásból (prompt) képet készít.

Támogatott szolgáltatók:
  - openai     (gpt-image-1, dall-e-3)   kulcs: OPENAI_API_KEY
  - stability  (Stable Image Core/Ultra) kulcs: STABILITY_API_KEY

A kulcsot környezeti változóból vagy a program melletti `.env` fájlból olvassa.
SOHA ne írd bele a kulcsot a kódba!

Használat:
    python image_generator.py "egy piros macska a holdon, akvarell"
    python image_generator.py "naplemente a Balatonon" --szolgaltato stability --arany 16:9
    python image_generator.py "logó egy pékségnek" --db 3 --mappa ./kepek
"""
import argparse
import base64
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 180

KEY_VARS = {"openai": "OPENAI_API_KEY", "stability": "STABILITY_API_KEY"}

# Képarány -> OpenAI méret
OPENAI_SIZES = {
    "gpt-image-1": {"1:1": "1024x1024", "16:9": "1536x1024", "3:2": "1536x1024",
                    "9:16": "1024x1536", "2:3": "1024x1536"},
    "dall-e-3": {"1:1": "1024x1024", "16:9": "1792x1024", "3:2": "1792x1024",
                 "9:16": "1024x1792", "2:3": "1024x1792"},
}
STABILITY_RATIOS = {"1:1", "16:9", "9:16", "3:2", "2:3", "4:5", "5:4", "21:9", "9:21"}


class GenerationError(Exception):
    pass


def load_env_file(path=os.path.join(HERE, ".env")):
    """Egyszerű .env betöltő (KULCS=érték sorok); a már beállított változókat nem írja felül."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def get_key(provider):
    var = KEY_VARS[provider]
    key = os.environ.get(var, "").strip()
    if not key:
        raise GenerationError(
            f"Hiányzik az API-kulcs. Állítsd be a {var} környezeti változót, "
            f"vagy írd be a {os.path.join(HERE, '.env')} fájlba így: {var}=sk-..."
        )
    return key


def api_error(resp):
    try:
        data = resp.json()
        msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
        msg = msg or data.get("message") or data.get("errors") or data.get("name") or resp.text
    except ValueError:
        msg = resp.text
    if isinstance(msg, (list, tuple)):
        msg = " ".join(str(m) for m in msg)
    friendly = {
        401: "Az API-kulcs hibás vagy visszavont – biztosan ehhez a szolgáltatóhoz tartozik?",
        402: "Elfogyott a kredit a szolgáltatónál. Vásárolj kreditet: Stability AI esetén "
             "platform.stability.ai/account/credits, OpenAI esetén platform.openai.com/settings/organization/billing.",
        429: "Túl sok kérés, vagy elfogyott a keret a szolgáltatónál. Próbáld újra kicsit később.",
    }
    if resp.status_code in friendly:
        return GenerationError(f"{friendly[resp.status_code]} (API hiba {resp.status_code})")
    return GenerationError(f"API hiba {resp.status_code}: {msg}")


def generate_openai(prompt, ratio, model, key):
    size = OPENAI_SIZES.get(model, OPENAI_SIZES["gpt-image-1"]).get(ratio, "1024x1024")
    body = {"model": model, "prompt": prompt, "size": size, "n": 1}
    if model.startswith("dall-e"):
        body["response_format"] = "b64_json"
    resp = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise api_error(resp)
    item = resp.json()["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), "png"
    img = requests.get(item["url"], timeout=TIMEOUT)
    img.raise_for_status()
    return img.content, "png"


def generate_stability(prompt, ratio, model, key, negative=None):
    endpoint = {"core": "core", "ultra": "ultra", "sd3": "sd3"}.get(model, "core")
    data = {"prompt": prompt, "output_format": "png",
            "aspect_ratio": ratio if ratio in STABILITY_RATIOS else "1:1"}
    if negative:
        data["negative_prompt"] = negative
    resp = requests.post(
        f"https://api.stability.ai/v2beta/stable-image/generate/{endpoint}",
        headers={"Authorization": f"Bearer {key}", "Accept": "image/*"},
        files={"none": ""},  # multipart/form-data kötelező
        data=data,
        timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        raise api_error(resp)
    return resp.content, "png"


def slugify(text, maxlen=40):
    table = str.maketrans("áéíóöőúüűÁÉÍÓÖŐÚÜŰ", "aeiooouuuAEIOOOUUU")
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.translate(table)).strip("_").lower()
    return text[:maxlen] or "kep"


def generate_images(prompt, provider="openai", ratio="1:1", model=None, count=1,
                    out_dir="./generalt_kepek", negative=None):
    key = get_key(provider)
    model = model or ("gpt-image-1" if provider == "openai" else "core")
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for i in range(count):
        print(f"[{i + 1}/{count}] Generálás ({provider}/{model})...")
        if provider == "openai":
            content, ext = generate_openai(prompt, ratio, model, key)
        else:
            content, ext = generate_stability(prompt, ratio, model, key, negative)
        path = os.path.join(out_dir, f"{stamp}_{slugify(prompt)}_{i + 1}.{ext}")
        with open(path, "wb") as f:
            f.write(content)
        print(f"  Mentve: {path}")
        saved.append(path)
    return saved


def main(argv=None):
    p = argparse.ArgumentParser(description="Szövegből képet generál AI segítségével.")
    p.add_argument("prompt", nargs="?", help="a kép leírása (ha üres, megkérdezi)")
    p.add_argument("--szolgaltato", choices=sorted(KEY_VARS), default="openai")
    p.add_argument("--modell", help="openai: gpt-image-1 | dall-e-3; stability: core | ultra | sd3")
    p.add_argument("--arany", default="1:1", help="képarány: 1:1, 16:9, 9:16, 3:2, 2:3 (alap: 1:1)")
    p.add_argument("--db", type=int, default=1, help="hány kép készüljön (alap: 1)")
    p.add_argument("--mappa", default=os.path.join(HERE, "generalt_kepek"), help="kimeneti mappa")
    p.add_argument("--negativ", help="(csak stability) mi NE legyen a képen")
    args = p.parse_args(argv)

    load_env_file()
    prompt = args.prompt or input("Írd le, milyen képet szeretnél: ").strip()
    if not prompt:
        print("Nincs megadva leírás.")
        return 1
    try:
        generate_images(prompt, args.szolgaltato, args.arany, args.modell,
                        max(1, args.db), args.mappa, args.negativ)
    except GenerationError as exc:
        print(f"Hiba: {exc}")
        return 1
    except requests.RequestException as exc:
        print(f"Hálózati hiba: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
