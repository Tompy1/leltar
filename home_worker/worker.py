"""
Otthoni képgeneráló worker – a saját gépeden (videókártyán) futtatja az AI-modellt.

A program maga kapcsolódik a VPS-en futó weboldalhoz, felveszi a képgenerálási
feladatokat, elkészíti a képet, és visszaküldi. Routert/portot NEM kell beállítani.

Beállítás: beallitasok.txt (SZERVER, TOKEN, MODELL)
Indítás:   start_windows.bat  vagy  ./start_linux.sh
Teszt mód (modell nélkül, csak a kapcsolat ellenőrzése):  --teszt
"""
import argparse
import io
import math
import os
import platform
import sys
import time
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "beallitasok.txt")

PRESETS = {
    # név: Hugging Face modell, lépésszám, guidance, alap képméret, kb. szükséges videómemória (GB)
    "sdxl": dict(repo="stabilityai/stable-diffusion-xl-base-1.0", steps=30, guidance=6.0, base=1024,
                 vram=12, label="SDXL"),
    "sdxl-turbo": dict(repo="stabilityai/sdxl-turbo", steps=4, guidance=0.0, base=512,
                       vram=8, label="SDXL-Turbo"),
    "sd-turbo": dict(repo="stabilityai/sd-turbo", steps=4, guidance=0.0, base=512,
                     vram=4, label="SD-Turbo"),
}
RATIOS = {"1:1": 1.0, "16:9": 16 / 9, "3:2": 3 / 2, "9:16": 9 / 16, "2:3": 2 / 3}


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip().upper()] = v.strip().strip('"').strip("'")
    for k in ("SZERVER", "TOKEN", "MODELL"):
        if os.environ.get("WORKER_" + k):
            cfg[k] = os.environ["WORKER_" + k]
    return cfg


def size_for(ratio, base):
    r = RATIOS.get(ratio, 1.0)
    w = base * math.sqrt(r)
    h = base / math.sqrt(r)
    return max(256, int(round(w / 64)) * 64), max(256, int(round(h / 64)) * 64)


def to_png(image):
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


class TestGenerator:
    """Modell nélküli teszt: színes kép a leírás szövegével – a kapcsolat kipróbálásához."""

    info = "TESZT mód"

    def generate(self, prompt, negative, ratio):
        from PIL import Image, ImageDraw
        import textwrap
        import zlib

        w, h = size_for(ratio, 512)
        c = zlib.crc32(prompt.encode())
        img = Image.new("RGB", (w, h), (80 + (c >> 16) % 150, 80 + (c >> 8) % 150, 80 + c % 150))
        draw = ImageDraw.Draw(img)
        text = "TESZT KÉP\n\n" + "\n".join(textwrap.wrap(prompt, 40)[:10])
        draw.multiline_text((20, 20), text, fill="white", spacing=6)
        time.sleep(1)
        return to_png(img)


class DiffusersGenerator:
    def __init__(self, preset_name):
        try:
            import torch
            from diffusers import AutoPipelineForText2Image
        except ImportError:
            sys.exit("Hiányoznak az AI-csomagok. Indítsd a start_windows.bat / start_linux.sh fájllal.")

        if torch.cuda.is_available():
            device = "cuda"
            props = torch.cuda.get_device_properties(0)
            vram = props.total_memory / 1024 ** 3
            gpu = f"{props.name}, {vram:.0f} GB"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device, vram, gpu = "mps", 16.0, "Apple GPU"
        else:
            device, vram, gpu = "cpu", 0.0, "processzor (nincs használható videókártya!)"
        log(f"Eszköz: {gpu}")

        if preset_name in ("", "auto"):
            if device == "cpu":
                preset_name = "sd-turbo"
            elif vram >= 12:
                preset_name = "sdxl"
            elif vram >= 6:
                preset_name = "sdxl-turbo"
            else:
                preset_name = "sd-turbo"
        if preset_name not in PRESETS:
            sys.exit(f"Ismeretlen MODELL: {preset_name}. Választható: auto, {', '.join(PRESETS)}")
        self.preset = p = PRESETS[preset_name]
        if device == "cpu":
            log("FIGYELEM: videókártya nélkül egy kép több percig is tarthat.")

        dtype = torch.float16 if device in ("cuda", "mps") else torch.float32
        log(f"Modell betöltése: {p['label']} ({p['repo']}). Az első indításkor letölti (több GB), ez eltarthat egy ideig...")
        kwargs = dict(torch_dtype=dtype, use_safetensors=True)
        if dtype == torch.float16:
            kwargs["variant"] = "fp16"
        self.pipe = AutoPipelineForText2Image.from_pretrained(p["repo"], **kwargs)
        if device == "cuda" and vram < p["vram"]:
            log("Kevés a videómemória ehhez a modellhez: takarékos mód (lassabb, de működik).")
            self.pipe.enable_model_cpu_offload()
        else:
            self.pipe.to(device)
        self.info = f"{p['label']}, {gpu}"
        log("Modell betöltve.")

    def generate(self, prompt, negative, ratio):
        p = self.preset
        width, height = size_for(ratio, p["base"])
        kwargs = dict(prompt=prompt, num_inference_steps=p["steps"], guidance_scale=p["guidance"],
                      width=width, height=height)
        if negative and p["guidance"] > 0:
            kwargs["negative_prompt"] = negative
        image = self.pipe(**kwargs).images[0]
        return to_png(image)


def main():
    ap = argparse.ArgumentParser(description="Otthoni képgeneráló worker")
    ap.add_argument("--teszt", action="store_true", help="modell nélküli teszt mód (kapcsolat ellenőrzése)")
    ap.add_argument("--modell", help="auto | sdxl | sdxl-turbo | sd-turbo")
    args = ap.parse_args()

    cfg = load_config()
    server = cfg.get("SZERVER", "").rstrip("/")
    token = cfg.get("TOKEN", "")
    if not server or not token or "IDE_" in server + token:
        sys.exit(f"Töltsd ki a beállításokat: {CONFIG_FILE} (SZERVER és TOKEN).")
    if not server.startswith(("http://", "https://")):
        server = "http://" + server

    gen = TestGenerator() if args.teszt else DiffusersGenerator(args.modell or cfg.get("MODELL", "auto"))

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        # HTTP-fejlécben csak ASCII lehet: az ékezetes szöveg URL-kódolva megy át
        "X-Worker-Info": quote(f"{gen.info} - {platform.node()}"[:120]),
    })
    log(f"Kapcsolódás: {server}  (leállítás: Ctrl+C)")
    connected, wait = False, 5
    while True:
        try:
            resp = session.post(f"{server}/worker/next", timeout=60)
        except requests.RequestException as exc:
            log(f"A szerver nem érhető el ({exc.__class__.__name__}). Újrapróbálás {wait} mp múlva...")
            connected = False
            time.sleep(wait)
            wait = min(wait * 2, 60)
            continue
        wait = 5
        if resp.status_code == 401:
            log("HIBÁS TOKEN: a beallitasok.txt TOKEN értéke nem egyezik a szerver .env fájljának WORKER_TOKEN értékével.")
            time.sleep(30)
            continue
        if resp.status_code == 404:
            log("A szerveren nincs bekapcsolva az otthoni gép mód (WORKER_TOKEN hiányzik a .env-ből), "
                "vagy rossz a SZERVER cím.")
            time.sleep(30)
            continue
        if resp.status_code >= 300:
            log(f"Váratlan válasz a szervertől: {resp.status_code}")
            time.sleep(10)
            continue
        if not connected:
            log("Kapcsolódva! Várom a feladatokat...")
            connected = True
        if resp.status_code == 204:
            continue  # nincs munka, újra kérdezünk

        task = resp.json()
        log(f"Új kép: {task['prompt'][:80]!r} ({task['ratio']})")
        t0 = time.time()
        try:
            png = gen.generate(task["prompt"], task.get("negative"), task["ratio"])
            data, files = None, {"image": ("image.png", png, "image/png")}
            log(f"Kész {time.time() - t0:.1f} mp alatt, küldés...")
        except Exception as exc:  # noqa: BLE001 – a hibát a weboldalon is megmutatjuk
            msg = str(exc)
            if "out of memory" in msg.lower():
                msg = "Elfogyott a videómemória. Állíts be kisebb modellt (MODELL=sd-turbo) a beallitasok.txt-ben."
            log(f"HIBA: {msg}")
            data, files = {"error": msg[:450]}, None
        try:
            r = session.post(f"{server}/worker/result/{task['id']}", data=data, files=files, timeout=120)
            if r.status_code == 409:
                log("A weboldal közben már nem várt erre a képre (időtúllépés).")
        except requests.RequestException as exc:
            log(f"Nem sikerült visszaküldeni az eredményt: {exc}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Leállítva.")
