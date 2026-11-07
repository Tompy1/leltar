# Saját videógeneráló (Python + MoviePy)

Képekből, feliratokból és háttérzenéből készít MP4 videót.

## Telepítés

```
pip install -r requirements.txt
```

Az FFmpeg-et a MoviePy automatikusan letölti, ImageMagick nem kell.

## Használat

1. Tedd a képeidet (jpg/png/webp) egy `kepek` mappába (ábécésorrendben kerülnek a videóba).
2. Opcionálisan tegyél mellé egy `zene.mp3` fájlt.
3. Futtasd:

```
python video_generator.py
```

Az eredmény: `sajat_generalt_video.mp4`.

### Opciók

| Kapcsoló | Jelentés | Alapérték |
|---|---|---|
| `--kepek` | képek mappája | `./kepek` |
| `--zene` | háttérzene | `./zene.mp3` |
| `--kimenet` | kimeneti fájl | `sajat_generalt_video.mp4` |
| `--hossz` | másodperc képenként | `4` |
| `--attunes` | áttűnés hossza (mp), 0 = nincs | `0` |
| `--szoveg` | felirat, `{n}` = sorszám, `{nev}` = fájlnév, `""` = nincs | `Jelenet {n}` |
| `--meret` | felbontás | `1280x720` |
| `--fps` | képkocka/mp | `24` |

Példa: `python video_generator.py --hossz 3 --attunes 1 --szoveg "{nev}" --meret 1920x1080`

## Az eredeti (PDF-beli) kódhoz képest

- MoviePy 2.x API (`from moviepy import ...`, `with_duration`, `subclipped`, `resized`) – a régi `moviepy.editor` már nem létezik.
- Eltérő méretű képek arányosan, fekete kerettel illeszkednek egy közös felbontásba.
- Ha a zene rövidebb a videónál, ismétlődik (az eredeti itt hibával leállt), a végén elhalkul.
- Ékezetes felirat működik (DejaVu/Arial font automatikus keresése).
- Opcionális áttűnés a képek között, parancssori kapcsolók.
