"""
AI Képgeneráló – önálló webalkalmazás VPS-re.

Bejelentkezés, szövegből képgenerálás (OpenAI / Stability AI), saját galéria,
videókészítés a kiválasztott képekből (MoviePy).

Beállítás környezeti változókkal (lásd ../.env.example):
  OPENAI_API_KEY / STABILITY_API_KEY, IMAGE_PROVIDER, DAILY_LIMIT, SITE_ADDRESS, DATA_DIR

Felhasználókezelés:
  flask --app app add-user te@pelda.hu
  flask --app app set-password te@pelda.hu
  flask --app app delete-user te@pelda.hu
  flask --app app list-users
"""
import os
import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from functools import wraps

import click
from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import image_generator as ig

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
IMAGE_DIR = os.path.join(DATA_DIR, "images")
VIDEO_DIR = os.path.join(DATA_DIR, "videos")
DB_PATH = os.path.join(DATA_DIR, "app.db")
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "20"))
MAX_VIDEO_IMAGES = int(os.environ.get("MAX_VIDEO_IMAGES", "30"))
RATIOS = ["1:1", "16:9", "3:2", "9:16", "2:3"]

for d in (IMAGE_DIR, VIDEO_DIR):
    os.makedirs(d, exist_ok=True)


def load_secret_key():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(DATA_DIR, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


site = os.environ.get("SITE_ADDRESS", "")
HTTPS = bool(site) and not site.startswith((":", "http://"))

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=HTTPS,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
    MAX_CONTENT_LENGTH=40 * 1024 * 1024,
)

# Egyszerre csak egy videó renderelődik (kímélni a VPS-t).
video_lock = threading.Semaphore(int(os.environ.get("VIDEO_WORKERS", "1")))


# ---------------------------------------------------------------- adatbázis

SCHEMA = """
create table if not exists users (
  id integer primary key,
  email text unique not null,
  password_hash text not null,
  created_at real not null
);
create table if not exists images (
  id integer primary key,
  user_id integer not null references users(id) on delete cascade,
  prompt text not null,
  provider text not null,
  ratio text not null,
  filename text not null,
  created_at real not null
);
create index if not exists images_user on images(user_id, created_at);
-- Napi korláthoz: a képek törlése után is megmarad.
create table if not exists generation_log (
  id integer primary key,
  user_id integer not null references users(id) on delete cascade,
  created_at real not null
);
create index if not exists generation_log_user on generation_log(user_id, created_at);
create table if not exists videos (
  id integer primary key,
  user_id integer not null references users(id) on delete cascade,
  filename text not null,
  image_count integer not null,
  created_at real not null
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma journal_mode = wal")
    return conn


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


with connect() as _c:
    _c.executescript(SCHEMA)


# ---------------------------------------------------------------- bejelentkezés

failed_logins = {}  # ip -> [időbélyegek]
failed_lock = threading.Lock()
MAX_FAILS, FAIL_WINDOW = 10, 15 * 60


def too_many_fails(ip):
    now = time.time()
    with failed_lock:
        recent = [t for t in failed_logins.get(ip, []) if now - t < FAIL_WINDOW]
        failed_logins[ip] = recent
        return len(recent) >= MAX_FAILS


def record_fail(ip):
    with failed_lock:
        failed_logins.setdefault(ip, []).append(time.time())


def current_user():
    uid = session.get("uid")
    if uid is None:
        return None
    return db().execute("select id, email from users where id = ?", (uid,)).fetchone()


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="Jelentkezz be újra."), 401
            return redirect(url_for("login"))
        g.user = user
        return view(*args, **kwargs)
    return wrapper


@app.before_request
def csrf_guard():
    # Az API-hívásokat csak a saját oldal JavaScriptje küldheti (más oldal nem tud egyedi fejlécet tenni).
    if request.method == "POST" and request.path.startswith("/api/"):
        if request.headers.get("X-Requested-With") != "fetch":
            abort(403)


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if too_many_fails(ip):
            error = "Túl sok sikertelen próbálkozás. Próbáld újra 15 perc múlva."
        else:
            email = request.form.get("email", "").strip().lower()
            row = db().execute("select id, password_hash from users where email = ?", (email,)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                session.clear()
                session.permanent = True
                session["uid"] = row["id"]
                return redirect(url_for("index"))
            record_fail(ip)
            error = "Hibás e-mail vagy jelszó."
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- oldalak

def used_today(user_id):
    since = time.time() - 24 * 3600
    return db().execute("select count(*) from generation_log where user_id = ? and created_at >= ?",
                        (user_id, since)).fetchone()[0]


def image_json(row):
    return {"id": row["id"], "prompt": row["prompt"], "url": url_for("media_image", image_id=row["id"])}


def video_json(row):
    return {"id": row["id"], "image_count": row["image_count"],
            "created": time.strftime("%Y.%m.%d %H:%M", time.localtime(row["created_at"])),
            "url": url_for("media_video", video_id=row["id"])}


@app.get("/")
@login_required
def index():
    uid = g.user["id"]
    images = db().execute("select * from images where user_id = ? order by created_at desc", (uid,)).fetchall()
    videos = db().execute("select * from videos where user_id = ? order by created_at desc", (uid,)).fetchall()
    return render_template(
        "index.html", user=g.user, ratios=RATIOS,
        images=[image_json(r) for r in images], videos=[video_json(r) for r in videos],
        remaining=max(0, DAILY_LIMIT - used_today(uid)), daily_limit=DAILY_LIMIT,
        max_video_images=MAX_VIDEO_IMAGES,
    )


@app.get("/healthz")
def healthz():
    return "ok"


# ---------------------------------------------------------------- képgenerálás

def pick_provider():
    wanted = os.environ.get("IMAGE_PROVIDER", "").lower()
    keys = {"openai": os.environ.get("OPENAI_API_KEY", "").strip(),
            "stability": os.environ.get("STABILITY_API_KEY", "").strip()}
    if wanted in keys and keys[wanted]:
        return wanted, keys[wanted]
    for name in ("openai", "stability"):
        if keys[name]:
            return name, keys[name]
    return None, None


@app.post("/api/generate")
@login_required
def api_generate():
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt", "")).strip()
    ratio = data.get("ratio") if data.get("ratio") in RATIOS else "1:1"
    negative = str(data.get("negative") or "").strip()[:1000] or None
    if not prompt:
        return jsonify(error="Add meg, milyen képet szeretnél."), 400
    if len(prompt) > 2000:
        return jsonify(error="A leírás legfeljebb 2000 karakter lehet."), 400

    uid = g.user["id"]
    if used_today(uid) >= DAILY_LIMIT:
        return jsonify(error=f"Elérted a napi korlátot ({DAILY_LIMIT} kép / 24 óra)."), 429

    provider, key = pick_provider()
    if not provider:
        return jsonify(error="A szerveren nincs beállítva API-kulcs (.env fájl)."), 500
    try:
        if provider == "openai":
            content, _ = ig.generate_openai(prompt, ratio, "gpt-image-1", key)
        else:
            content, _ = ig.generate_stability(prompt, ratio, "core", key, negative)
    except ig.GenerationError as exc:
        app.logger.warning("Generálási hiba: %s", exc)
        return jsonify(error=str(exc)), 502
    except Exception:
        app.logger.exception("Váratlan hiba generálás közben")
        return jsonify(error="Nem sikerült elérni a képgeneráló szolgáltatást."), 502

    filename = f"{uid}_{uuid.uuid4().hex}.png"
    with open(os.path.join(IMAGE_DIR, filename), "wb") as f:
        f.write(content)
    cur = db().execute(
        "insert into images (user_id, prompt, provider, ratio, filename, created_at) values (?,?,?,?,?,?)",
        (uid, prompt, provider, ratio, filename, time.time()))
    db().execute("insert into generation_log (user_id, created_at) values (?, ?)", (uid, time.time()))
    db().commit()
    row = db().execute("select * from images where id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(image=image_json(row), provider=provider,
                   remaining=max(0, DAILY_LIMIT - used_today(uid)))


def own_row(table, row_id):
    row = db().execute(f"select * from {table} where id = ? and user_id = ?", (row_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return row


def remove_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


@app.post("/api/images/<int:image_id>/delete")
@login_required
def api_delete_image(image_id):
    row = own_row("images", image_id)
    remove_file(os.path.join(IMAGE_DIR, row["filename"]))
    db().execute("delete from images where id = ?", (image_id,))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- videó

@app.post("/api/video")
@login_required
def api_video():
    import video_generator as vg  # lassú import, csak ha kell

    uid = g.user["id"]
    try:
        ids = [int(x) for x in request.form.get("image_ids", "").split(",") if x.strip()]
        seconds = min(15.0, max(1.0, float(request.form.get("seconds", "4"))))
        crossfade = min(seconds / 2, max(0.0, float(request.form.get("crossfade", "1"))))
    except ValueError:
        return jsonify(error="Hibás beállítás."), 400
    if not ids:
        return jsonify(error="Jelölj ki legalább egy képet."), 400
    if len(ids) > MAX_VIDEO_IMAGES:
        return jsonify(error=f"Legfeljebb {MAX_VIDEO_IMAGES} kép lehet egy videóban."), 400

    rows = {r["id"]: r for r in db().execute(
        f"select * from images where user_id = ? and id in ({','.join('?' * len(ids))})", (uid, *ids))}
    ordered = [rows[i] for i in ids if i in rows]
    if not ordered:
        return jsonify(error="A kijelölt képek nem találhatók."), 404
    size = (1920, 1080) if request.form.get("size") == "1080p" else (1280, 720)
    if request.form.get("orientation") == "portrait":
        size = (size[1], size[0])
    captions = [r["prompt"] for r in ordered] if request.form.get("captions") == "1" else []

    music = request.files.get("music")
    music_path = None
    if music and music.filename:
        ext = os.path.splitext(music.filename)[1].lower()
        if ext not in (".mp3", ".m4a", ".wav", ".ogg", ".aac"):
            return jsonify(error="A zene mp3, m4a, wav, ogg vagy aac lehet."), 400
        fd, music_path = tempfile.mkstemp(suffix=ext, dir=VIDEO_DIR)
        with os.fdopen(fd, "wb") as f:
            music.save(f)

    filename = f"{uid}_{uuid.uuid4().hex}.mp4"
    out_path = os.path.join(VIDEO_DIR, filename)
    if not video_lock.acquire(timeout=600):
        if music_path:
            remove_file(music_path)
        return jsonify(error="A szerver most egy másik videón dolgozik. Próbáld újra később."), 503
    try:
        ok = vg.create_video_from_images(
            None, music_path, out_path,
            duration_per_image=seconds, size=size, crossfade=crossfade if len(ordered) > 1 else 0,
            image_files=[os.path.join(IMAGE_DIR, r["filename"]) for r in ordered],
            captions=captions, quiet=True,
        )
    except Exception:
        app.logger.exception("Videókészítési hiba")
        ok = False
    finally:
        video_lock.release()
        if music_path:
            remove_file(music_path)
    if not ok:
        remove_file(out_path)
        return jsonify(error="Nem sikerült elkészíteni a videót (részletek a szerver naplójában)."), 500

    cur = db().execute("insert into videos (user_id, filename, image_count, created_at) values (?,?,?,?)",
                       (uid, filename, len(ordered), time.time()))
    db().commit()
    row = db().execute("select * from videos where id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(video=video_json(row))


@app.post("/api/videos/<int:video_id>/delete")
@login_required
def api_delete_video(video_id):
    row = own_row("videos", video_id)
    remove_file(os.path.join(VIDEO_DIR, row["filename"]))
    db().execute("delete from videos where id = ?", (video_id,))
    db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- fájlok kiszolgálása

@app.get("/media/images/<int:image_id>")
@login_required
def media_image(image_id):
    row = own_row("images", image_id)
    return send_file(os.path.join(IMAGE_DIR, row["filename"]), mimetype="image/png",
                     as_attachment=request.args.get("download") == "1",
                     download_name=f"kep_{image_id}.png", max_age=86400)


@app.get("/media/videos/<int:video_id>")
@login_required
def media_video(video_id):
    row = own_row("videos", video_id)
    return send_file(os.path.join(VIDEO_DIR, row["filename"]), mimetype="video/mp4",
                     as_attachment=request.args.get("download") == "1",
                     download_name=f"video_{video_id}.mp4", conditional=True)


# ---------------------------------------------------------------- parancssori felhasználókezelés

def ask_password():
    pw = click.prompt("Jelszó", hide_input=True, confirmation_prompt="Jelszó még egyszer")
    if len(pw) < 8:
        raise click.ClickException("A jelszó legalább 8 karakter legyen.")
    return pw


@app.cli.command("add-user")
@click.argument("email")
def add_user(email):
    """Új felhasználó létrehozása."""
    email = email.strip().lower()
    pw = ask_password()
    try:
        with connect() as c:
            c.execute("insert into users (email, password_hash, created_at) values (?,?,?)",
                      (email, generate_password_hash(pw), time.time()))
    except sqlite3.IntegrityError:
        raise click.ClickException("Ez az e-mail cím már létezik (jelszócsere: set-password).")
    click.echo(f"Felhasználó létrehozva: {email}")


@app.cli.command("set-password")
@click.argument("email")
def set_password(email):
    """Jelszó módosítása."""
    pw = ask_password()
    with connect() as c:
        n = c.execute("update users set password_hash = ? where email = ?",
                      (generate_password_hash(pw), email.strip().lower())).rowcount
    if not n:
        raise click.ClickException("Nincs ilyen felhasználó.")
    click.echo("Jelszó módosítva.")


@app.cli.command("delete-user")
@click.argument("email")
def delete_user(email):
    """Felhasználó törlése a képeivel és videóival együtt."""
    with connect() as c:
        row = c.execute("select id from users where email = ?", (email.strip().lower(),)).fetchone()
        if not row:
            raise click.ClickException("Nincs ilyen felhasználó.")
        for table, folder in (("images", IMAGE_DIR), ("videos", VIDEO_DIR)):
            for r in c.execute(f"select filename from {table} where user_id = ?", (row["id"],)):
                remove_file(os.path.join(folder, r["filename"]))
        c.execute("delete from users where id = ?", (row["id"],))
    click.echo("Felhasználó törölve.")


@app.cli.command("list-users")
def list_users():
    """Felhasználók listája."""
    with connect() as c:
        rows = c.execute("""select u.email, count(i.id) as n from users u
                            left join images i on i.user_id = u.id group by u.id order by u.email""").fetchall()
    for r in rows:
        click.echo(f"{r['email']}  ({r['n']} kép)")
    if not rows:
        click.echo("Még nincs felhasználó.")
