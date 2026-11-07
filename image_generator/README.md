# AI képgeneráló (szövegből kép)

Szöveges leírásból (promptból) készít képet az **OpenAI** vagy a **Stability AI** API-jával.

## Telepítés

```
pip install -r requirements.txt
```

## API-kulcs beállítása

Másold le a `.env.example` fájlt `.env` néven, és írd be a kulcsodat:

```
OPENAI_API_KEY=sk-...        # ha OpenAI kulcsod van
STABILITY_API_KEY=sk-...     # ha Stability AI kulcsod van
```

A `.env` fájlt a git kihagyja, így a kulcs nem kerül fel a GitHubra. Környezeti változóként is megadhatod (`export OPENAI_API_KEY=...`).

Nem tudod, melyik szolgáltatótól van a kulcs? Próbáld ki az egyiket: ha `401`-es hibát kapsz, akkor a másikhoz tartozik.
Kulcsot itt lehet kezelni: platform.openai.com/api-keys, illetve platform.stability.ai/account/keys.

## Használat

```
python image_generator.py "egy piros macska a holdon, akvarell"
python image_generator.py "naplemente a Balatonon" --szolgaltato stability --arany 16:9
python image_generator.py "logó egy pékségnek" --db 3
python image_generator.py            # megkérdezi a leírást
```

A képek a `generalt_kepek/` mappába kerülnek.

| Kapcsoló | Jelentés | Alapérték |
|---|---|---|
| `--szolgaltato` | `openai` vagy `stability` | `openai` |
| `--modell` | openai: `gpt-image-1`, `dall-e-3`; stability: `core`, `ultra`, `sd3` | `gpt-image-1` / `core` |
| `--arany` | képarány: `1:1`, `16:9`, `9:16`, `3:2`, `2:3` | `1:1` |
| `--db` | hány kép készüljön | `1` |
| `--mappa` | kimeneti mappa | `generalt_kepek` |
| `--negativ` | (csak Stability) mi ne legyen a képen | – |

## Videó a generált képekből

A képekből videót is készíthetsz a szomszédos videógenerálóval:

```
python ../video_generator/video_generator.py --kepek generalt_kepek --szoveg ""
```
