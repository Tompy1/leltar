"""
Saját videógeneráló: képekből + feliratokból + háttérzenéből MP4 videó.

MoviePy 2.x-re írva (a régi `moviepy.editor` import már nem létezik).

Használat:
    python video_generator.py                       # alapértelmezett: ./kepek, ./zene.mp3
    python video_generator.py --kepek ./fotok --zene dal.mp3 --kimenet film.mp4 \
        --hossz 4 --attunes 1 --szoveg "Jelenet {n}"
"""
import argparse
import os
import sys
import textwrap

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    afx,
    concatenate_audioclips,
    concatenate_videoclips,
    vfx,
)

VALID_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

# Ékezetes betűket is tartalmazó fontok keresése (Linux / macOS / Windows)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]


def find_font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None  # MoviePy a Pillow beépített fontját használja


def fit_image(img_path, size, duration):
    """A képet arányosan a célméretbe illeszti, fekete háttérre középre téve."""
    w, h = size
    img = ImageClip(img_path).with_duration(duration)
    scale = min(w / img.w, h / img.h)
    img = img.resized(scale).with_position("center")
    background = ColorClip(size, color=(0, 0, 0)).with_duration(duration)
    return CompositeVideoClip([background, img], size=size)


def make_caption(text, size, duration, font):
    w, h = size
    if len(text) > 140:
        text = text[:137].rstrip() + "..."
    font_size = max(24, h // 18)
    kwargs = dict(font=font, text=text, font_size=font_size, color="white",
                  bg_color="black", margin=(20, 10))
    txt = TextClip(**kwargs)
    if txt.w > w * 0.9:  # túl hosszú: szóhatáron tördelés, kisebb betűvel, középre
        font_size = max(20, h // 26)
        per_line = max(10, int(w * 0.85 / (font_size * 0.6)))
        kwargs.update(font_size=font_size, text="\n".join(textwrap.wrap(text, per_line)),
                      text_align="center", interline=6)
        txt = TextClip(**kwargs)
    return txt.with_position(("center", h - txt.h - h // 20)).with_duration(duration)


def build_audio(audio_path, duration):
    """Hang betöltése: ha hosszabb, levágja; ha rövidebb, ismétli; a végén elhalkítja."""
    audio = AudioFileClip(audio_path)
    if audio.duration < duration:
        repeats = int(duration // audio.duration) + 1
        audio = concatenate_audioclips([audio] * repeats)
    audio = audio.subclipped(0, duration)
    return audio.with_effects([afx.AudioFadeOut(min(1.5, duration / 4))])


def create_video_from_images(
    image_folder,
    audio_path,
    output_path,
    duration_per_image=3,
    size=(1280, 720),
    caption="Jelenet {n}",
    crossfade=0.0,
    fps=24,
    image_files=None,
    captions=None,
    quiet=False,
):
    """Videó készítése.

    image_files: ha meg van adva, ezeket a képeket használja (ebben a sorrendben) a mappa helyett.
    captions: képenkénti felirat-lista; ha meg van adva, a `caption` sablont felülírja.
    quiet: nincs folyamatjelző kiírás (szerveren hasznos).
    """
    if image_files is None:
        image_files = sorted(
            os.path.join(image_folder, f)
            for f in os.listdir(image_folder)
            if f.lower().endswith(VALID_EXTENSIONS)
        )
    if not image_files:
        print(f"Nem találhatók képek a(z) '{image_folder}' mappában!")
        return False

    font = find_font()
    clips = []
    for index, img_path in enumerate(image_files):
        clip = fit_image(img_path, size, duration_per_image)
        if captions is not None:
            text = captions[index] if index < len(captions) else ""
        elif caption:
            name = os.path.splitext(os.path.basename(img_path))[0]
            text = caption.format(n=index + 1, nev=name)
        else:
            text = ""
        if text:
            try:
                txt = make_caption(text, size, duration_per_image, font)
                clip = CompositeVideoClip([clip, txt], size=size)
            except Exception as exc:  # felirat nélkül folytatjuk
                print(f"Figyelem: felirat nem készült ({exc}).")
        if crossfade > 0 and index > 0:
            clip = clip.with_effects([vfx.CrossFadeIn(crossfade)])
        clips.append(clip)

    padding = -crossfade if crossfade > 0 else 0
    full_video = concatenate_videoclips(clips, method="compose", padding=padding)

    if audio_path and os.path.exists(audio_path):
        full_video = full_video.with_audio(build_audio(audio_path, full_video.duration))
    elif audio_path:
        print(f"Figyelem: a hangfájl ({audio_path}) nem található. A videó néma lesz.")

    print(f"Videó renderelése folyamatban... ({len(clips)} kép, {full_video.duration:.1f} mp)")
    tmp_audio = os.path.splitext(output_path)[0] + "_tmp_audio.m4a"
    full_video.write_videofile(
        output_path, fps=fps, codec="libx264", audio_codec="aac",
        temp_audiofile=tmp_audio, logger=None if quiet else "bar",
    )
    full_video.close()
    print(f"Sikeres generálás! A videó mentve: {output_path}")
    return True


def main():
    p = argparse.ArgumentParser(description="Képekből videót generál.")
    p.add_argument("--kepek", default="./kepek", help="képek mappája (alap: ./kepek)")
    p.add_argument("--zene", default="./zene.mp3", help="háttérzene (alap: ./zene.mp3)")
    p.add_argument("--kimenet", default="sajat_generalt_video.mp4", help="kimeneti MP4")
    p.add_argument("--hossz", type=float, default=4, help="másodperc képenként (alap: 4)")
    p.add_argument("--attunes", type=float, default=0, help="áttűnés hossza mp-ben (alap: 0)")
    p.add_argument("--szoveg", default="Jelenet {n}",
                   help='felirat sablon: {n}=sorszám, {nev}=fájlnév; "" = nincs felirat')
    p.add_argument("--meret", default="1280x720", help="felbontás, pl. 1920x1080")
    p.add_argument("--fps", type=int, default=24)
    args = p.parse_args()

    if not os.path.isdir(args.kepek):
        os.makedirs(args.kepek)
        print(f"Létrehoztam a '{args.kepek}' mappát. Tegyél bele képeket, majd futtasd újra!")
        return 0

    width, height = (int(x) for x in args.meret.lower().split("x"))
    ok = create_video_from_images(
        args.kepek, args.zene, args.kimenet,
        duration_per_image=args.hossz, size=(width, height),
        caption=args.szoveg, crossfade=args.attunes, fps=args.fps,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
