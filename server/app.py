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
from urllib.parse import unquote
from functools import wraps

import click
import hmac
from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import requests

import image_generator as ig

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
IMAGE_DIR = os.path.join(DATA_DIR, "images")
VIDEO_DIR = os.path.join(DATA_DIR, "videos")
DB_PATH = os.path.join(DATA_DIR, "app.db")
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "20"))
MAX_VIDEO_IMAGES = int(os.environ.get("MAX_VIDEO_IMAGES", "30"))
RATIOS = ["1:1", "16:9", "3:2", "9:16", "2:3"]
MAX_STORY_SCENES = int(os.environ.get("MAX_STORY_SCENES", "12"))
SORA_MODEL = os.environ.get("SORA_MODEL", "sora-2")
AI_VIDEO_DAILY_LIMIT = int(os.environ.get("AI_VIDEO_DAILY_LIMIT", "3"))
OPENAI_VIDEOS_URL = "https://api.openai.com/v1/videos"
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "").strip()
LOCAL_TIMEOUT = int(os.environ.get("LOCAL_TIMEOUT", "600"))
WORKER_ONLINE_SECONDS = 90

for d in (IMAGE_DIR, VIDEO_DIR):
    os.makedirs(d, exist_ok=True)


def load_secret_key():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(DATA_DIR, "secret_key")
    # Atomikus létrehozás: több gunicorn worker egyszerre indul, mindnek UGYANAZT a kulcsot kell használnia.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    for _ in range(50):  # ha egy másik worker épp most írja, megvárjuk
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
        time.sleep(0.1)
    raise RuntimeError("A titkos kulcs fájlja üres: " + path)




class SessionInterface(SecureCookieSessionInterface):
    """A süti csak akkor kap Secure jelzést, ha a kérés ténylegesen https-en jött.

    Így http-n (IP címen, domain nélkül) is működik a bejelentkezés, https-en pedig biztonságos.
    """

    def get_cookie_secure(self, app):
        return request.is_secure

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.session_interface = SessionInterface()
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
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
-- Háttérfeladatok (mese jelenetekből, AI-videó)
create table if not exists jobs (
  id text primary key,
  user_id integer not null references users(id) on delete cascade,
  kind text not null,
  status text not null,
  progress real not null default 0,
  message text not null default '',
  video_id integer,
  created_at real not null
);
create index if not exists jobs_user on jobs(user_id, created_at);
-- Otthoni gépnek (worker) szánt képgenerálási feladatok
create table if not exists gen_tasks (
  id text primary key,
  prompt text not null,
  negative text,
  ratio text not null,
  status text not null,            -- queued / working / done / error / cancelled
  result_file text,
  error text,
  created_at real not null,
  claimed_at real
);
create table if not exists worker_status (
  id integer primary key check (id = 1),
  last_seen real not null,
  info text
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


_c = connect()
_c.executescript(SCHEMA)
if "title" not in [r[1] for r in _c.execute("pragma table_info(videos)")]:
    _c.execute("alter table videos add column title text")
# Újraindításkor a félbemaradt háttérfeladatok nem folytatódnak.
_c.execute("update jobs set status = 'error', message = 'A szerver újraindult, a feladat megszakadt.' "
           "where status = 'running'")
_c.commit()
_c.close()  # --preload mellett ne öröklődjön nyitott kapcsolat a workerekbe


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

class UserError(Exception):
    """Felhasználónak megjeleníthető hiba, HTTP státusszal."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def used_today(user_id, conn=None):
    since = time.time() - 24 * 3600
    return (conn or db()).execute("select count(*) from generation_log where user_id = ? and created_at >= ?",
                        (user_id, since)).fetchone()[0]


def image_json(row):
    lines = [l for l in row["prompt"].splitlines() if l.strip()]
    label = lines[-1] if lines else row["prompt"]  # mesénél a jelenet sora, nem a közös stílus
    return {"id": row["id"], "prompt": row["prompt"], "label": label,
            "url": url_for("media_image", image_id=row["id"])}


def video_json(row):
    title = row["title"] or f'{row["image_count"]} kép'
    return {"id": row["id"], "image_count": row["image_count"], "title": title,
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
        max_video_images=MAX_VIDEO_IMAGES, max_story_scenes=MAX_STORY_SCENES,
        sora_enabled=bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        provider_label=provider_label(),
        active_jobs=[r["id"] for r in db().execute(
            "select id from jobs where user_id = ? and status = 'running'", (uid,))],
    )


@app.get("/healthz")
def healthz():
    return "ok"


# ---------------------------------------------------------------- képgenerálás

def pick_provider():
    wanted = os.environ.get("IMAGE_PROVIDER", "").lower()
    keys = {"openai": os.environ.get("OPENAI_API_KEY", "").strip(),
            "stability": os.environ.get("STABILITY_API_KEY", "").strip()}
    if wanted == "local" or (WORKER_TOKEN and wanted not in keys):
        return ("local", None) if WORKER_TOKEN else (None, None)
    if wanted in keys and keys[wanted]:
        return wanted, keys[wanted]
    for name in ("openai", "stability"):
        if keys[name]:
            return name, keys[name]
    return None, None


def generate_image(conn, uid, prompt, ratio, negative=None):
    """Egy kép generálása, mentése és naplózása. Hiba esetén UserError."""
    if used_today(uid, conn) >= DAILY_LIMIT:
        raise UserError(f"Elérted a napi korlátot ({DAILY_LIMIT} kép / 24 óra).", 429)
    provider, key = pick_provider()
    if not provider:
        raise UserError("A szerveren nincs beállítva API-kulcs (.env fájl).", 500)
    try:
        if provider == "local":
            content = generate_local(conn, prompt, ratio, negative)
        elif provider == "openai":
            content, _ = ig.generate_openai(prompt, ratio, "gpt-image-1", key)
        else:
            content, _ = ig.generate_stability(prompt, ratio, "core", key, negative)
    except ig.GenerationError as exc:
        app.logger.warning("Generálási hiba: %s", exc)
        raise UserError(str(exc), 502)
    except Exception:
        app.logger.exception("Váratlan hiba generálás közben")
        raise UserError("Nem sikerült elérni a képgeneráló szolgáltatást.", 502)

    filename = f"{uid}_{uuid.uuid4().hex}.png"
    with open(os.path.join(IMAGE_DIR, filename), "wb") as f:
        f.write(content)
    now = time.time()
    cur = conn.execute(
        "insert into images (user_id, prompt, provider, ratio, filename, created_at) values (?,?,?,?,?,?)",
        (uid, prompt, provider, ratio, filename, now))
    conn.execute("insert into generation_log (user_id, created_at) values (?, ?)", (uid, now))
    conn.commit()
    return conn.execute("select * from images where id = ?", (cur.lastrowid,)).fetchone(), provider


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
    try:
        row, provider = generate_image(db(), uid, prompt, ratio, negative)
    except UserError as exc:
        return jsonify(error=str(exc)), exc.status
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

def video_size(form):
    size = (1920, 1080) if form.get("size") == "1080p" else (1280, 720)
    if form.get("orientation") == "portrait":
        size = (size[1], size[0])
    return size


def video_timing(form):
    try:
        seconds = min(15.0, max(1.0, float(form.get("seconds", "4"))))
        crossfade = min(seconds / 2, max(0.0, float(form.get("crossfade", "1"))))
    except ValueError:
        raise UserError("Hibás beállítás.")
    return seconds, crossfade


def save_music(files):
    """A feltöltött zenét ideiglenes fájlba menti; visszaadja az útvonalát (vagy None)."""
    music = files.get("music")
    if not (music and music.filename):
        return None
    ext = os.path.splitext(music.filename)[1].lower()
    if ext not in (".mp3", ".m4a", ".wav", ".ogg", ".aac"):
        raise UserError("A zene mp3, m4a, wav, ogg vagy aac lehet.")
    fd, path = tempfile.mkstemp(suffix=ext, dir=VIDEO_DIR)
    with os.fdopen(fd, "wb") as f:
        music.save(f)
    return path


def make_slideshow(image_paths, music_path, seconds, crossfade, size, captions):
    """Képekből videót fűz; visszaadja a videó fájlnevét. Hiba esetén UserError."""
    import video_generator as vg  # lassú import, csak ha kell

    filename = f"{uuid.uuid4().hex}.mp4"
    out_path = os.path.join(VIDEO_DIR, filename)
    if not video_lock.acquire(timeout=900):
        raise UserError("A szerver most egy másik videón dolgozik. Próbáld újra később.", 503)
    try:
        ok = vg.create_video_from_images(
            None, music_path, out_path,
            duration_per_image=seconds, size=size, crossfade=crossfade if len(image_paths) > 1 else 0,
            image_files=image_paths, captions=captions, quiet=True,
        )
    except Exception:
        app.logger.exception("Videókészítési hiba")
        ok = False
    finally:
        video_lock.release()
    if not ok:
        remove_file(out_path)
        raise UserError("Nem sikerült elkészíteni a videót (részletek a szerver naplójában).", 500)
    return filename


def insert_video(conn, uid, filename, image_count, title=None):
    cur = conn.execute(
        "insert into videos (user_id, filename, image_count, created_at, title) values (?,?,?,?,?)",
        (uid, filename, image_count, time.time(), title))
    conn.commit()
    return cur.lastrowid


@app.post("/api/video")
@login_required
def api_video():
    uid = g.user["id"]
    music_path = None
    try:
        ids = [int(x) for x in request.form.get("image_ids", "").split(",") if x.strip().isdigit()]
        seconds, crossfade = video_timing(request.form)
        if not ids:
            raise UserError("Jelölj ki legalább egy képet.")
        if len(ids) > MAX_VIDEO_IMAGES:
            raise UserError(f"Legfeljebb {MAX_VIDEO_IMAGES} kép lehet egy videóban.")
        rows = {r["id"]: r for r in db().execute(
            f"select * from images where user_id = ? and id in ({','.join('?' * len(ids))})", (uid, *ids))}
        ordered = [rows[i] for i in ids if i in rows]
        if not ordered:
            raise UserError("A kijelölt képek nem találhatók.", 404)
        captions = [r["prompt"] for r in ordered] if request.form.get("captions") == "1" else []
        music_path = save_music(request.files)
        filename = make_slideshow([os.path.join(IMAGE_DIR, r["filename"]) for r in ordered], music_path,
                                  seconds, crossfade, video_size(request.form), captions)
    except UserError as exc:
        return jsonify(error=str(exc)), exc.status
    finally:
        if music_path:
            remove_file(music_path)
    video_id = insert_video(db(), uid, filename, len(ordered))
    row = db().execute("select * from videos where id = ?", (video_id,)).fetchone()
    return jsonify(video=video_json(row))


# ---------------------------------------------------------------- otthoni gép (worker)

def worker_last_seen(conn=None):
    row = (conn or db()).execute("select last_seen, info from worker_status where id = 1").fetchone()
    return (row["last_seen"], row["info"]) if row else (0, None)


def worker_online(conn=None):
    return time.time() - worker_last_seen(conn)[0] < WORKER_ONLINE_SECONDS


def provider_label():
    provider, _ = pick_provider()
    if provider == "local":
        last, info = worker_last_seen()
        if time.time() - last < WORKER_ONLINE_SECONDS:
            return f"Otthoni gép: online ({info})" if info else "Otthoni gép: online"
        return "Otthoni gép: OFFLINE – indítsd el rajta a workert"
    return {"openai": "OpenAI", "stability": "Stability AI"}.get(provider, "nincs beállítva")


def generate_local(conn, prompt, ratio, negative):
    """Feladatot tesz a sorba az otthoni gépnek, és megvárja az eredményt (PNG bájtok)."""
    if not worker_online(conn):
        raise ig.GenerationError("Az otthoni gép nem elérhető. Kapcsold be, és indítsd el rajta a workert "
                                 "(start_windows.bat / start_linux.sh).")
    task_id = uuid.uuid4().hex
    conn.execute("insert into gen_tasks (id, prompt, negative, ratio, status, created_at) values (?,?,?,?,?,?)",
                 (task_id, prompt, negative, ratio, "queued", time.time()))
    conn.commit()
    deadline = time.time() + LOCAL_TIMEOUT
    try:
        while time.time() < deadline:
            time.sleep(1)
            row = conn.execute("select status, result_file, error from gen_tasks where id = ?",
                               (task_id,)).fetchone()
            if row["status"] == "done":
                path = os.path.join(IMAGE_DIR, row["result_file"])
                with open(path, "rb") as f:
                    content = f.read()
                remove_file(path)
                return content
            if row["status"] == "error":
                raise ig.GenerationError(f"Az otthoni gép hibát jelzett: {row['error']}")
            if row["status"] == "queued" and not worker_online(conn):
                raise ig.GenerationError("Az otthoni gép közben elérhetetlenné vált.")
        raise ig.GenerationError(f"Az otthoni gép {LOCAL_TIMEOUT // 60} perc alatt sem készült el a képpel.")
    finally:
        # Ha nem lett kész, a feladat ne maradjon a sorban.
        conn.execute("update gen_tasks set status = 'cancelled' where id = ? and status in ('queued', 'working')",
                     (task_id,))
        conn.execute("delete from gen_tasks where id = ? and status != 'working'", (task_id,))
        conn.commit()


def worker_auth():
    if not WORKER_TOKEN:
        abort(404)
    sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(sent.encode(), WORKER_TOKEN.encode()):
        abort(401)


@app.post("/worker/next")
def worker_next():
    """Az otthoni gép kérdezi: van munka? Legfeljebb ~25 mp-ig vár (long polling)."""
    worker_auth()
    info = unquote(request.headers.get("X-Worker-Info", ""))[:120]
    conn = db()
    deadline = time.time() + 25
    while True:
        now = time.time()
        conn.execute("insert into worker_status (id, last_seen, info) values (1, ?, ?) "
                     "on conflict(id) do update set last_seen = excluded.last_seen, info = excluded.info",
                     (now, info))
        # 15 percnél régebben felvett, be nem fejezett feladatok újra sorba kerülnek
        conn.execute("update gen_tasks set status = 'queued', claimed_at = null "
                     "where status = 'working' and claimed_at < ?", (now - 900,))
        row = conn.execute(
            "update gen_tasks set status = 'working', claimed_at = ? where id = ("
            "  select id from gen_tasks where status = 'queued' order by created_at limit 1"
            ") and status = 'queued' returning id, prompt, negative, ratio", (now,)).fetchone()
        conn.commit()
        if row:
            return jsonify(id=row["id"], prompt=row["prompt"], negative=row["negative"], ratio=row["ratio"])
        if now >= deadline:
            return "", 204
        time.sleep(1)


@app.post("/worker/result/<task_id>")
def worker_result(task_id):
    worker_auth()
    conn = db()
    row = conn.execute("select status from gen_tasks where id = ?", (task_id,)).fetchone()
    if row is None or row["status"] != "working":
        return jsonify(ok=False, reason="A feladatot közben visszavonták."), 409
    image = request.files.get("image")
    if image:
        filename = f"tmp_{task_id}.png"
        image.save(os.path.join(IMAGE_DIR, filename))
        conn.execute("update gen_tasks set status = 'done', result_file = ? where id = ?", (filename, task_id))
    else:
        error = request.form.get("error", "ismeretlen hiba")[:500]
        conn.execute("update gen_tasks set status = 'error', error = ? where id = ?", (error, task_id))
    conn.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- háttérfeladatok

def update_job(job_id, **fields):
    conn = connect()
    try:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"update jobs set {sets} where id = ?", (*fields.values(), job_id))
        conn.commit()
    finally:
        conn.close()


def start_job(uid, kind, work, cleanup=None):
    """Háttérszálon futtatja a work(conn, progress) függvényt, ami a kész videó azonosítóját adja vissza."""
    if db().execute("select 1 from jobs where user_id = ? and status = 'running'", (uid,)).fetchone():
        raise UserError("Már fut egy videókészítésed. Várd meg, amíg elkészül.", 409)
    job_id = uuid.uuid4().hex
    db().execute("insert into jobs (id, user_id, kind, status, message, created_at) values (?,?,?,?,?,?)",
                 (job_id, uid, kind, "running", "Indítás...", time.time()))
    db().commit()

    def progress(fraction, message):
        update_job(job_id, progress=max(0.0, min(1.0, fraction)), message=message)

    def runner():
        conn = connect()
        try:
            video_id = work(conn, progress)
            update_job(job_id, status="done", progress=1.0, message="Kész!", video_id=video_id)
        except UserError as exc:
            update_job(job_id, status="error", message=str(exc))
        except Exception:
            app.logger.exception("Háttérfeladat hiba (%s)", kind)
            update_job(job_id, status="error", message="Váratlan hiba (részletek a szerver naplójában).")
        finally:
            conn.close()
            if cleanup:
                cleanup()

    threading.Thread(target=runner, daemon=True, name=f"job-{kind}").start()
    return job_id


@app.get("/api/jobs/<job_id>")
@login_required
def api_job(job_id):
    row = db().execute("select * from jobs where id = ? and user_id = ?", (job_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    out = {"id": row["id"], "kind": row["kind"], "status": row["status"],
           "progress": row["progress"], "message": row["message"]}
    if row["video_id"]:
        video = db().execute("select * from videos where id = ?", (row["video_id"],)).fetchone()
        if video:
            out["video"] = video_json(video)
    return jsonify(out)


# ---------------------------------------------------------------- mese jelenetekből

def parse_scenes(text):
    """Soronként egy jelenet. Opcionálisan: 'kép leírása | felirat a videóban'."""
    scenes = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        prompt, _, caption = line.partition("|")
        if prompt.strip():
            scenes.append((prompt.strip()[:1500], caption.strip()[:200]))
    return scenes


@app.post("/api/story")
@login_required
def api_story():
    uid = g.user["id"]
    music_path = None
    try:
        style = request.form.get("style", "").strip()[:1000]
        scenes = parse_scenes(request.form.get("scenes", ""))
        if not scenes:
            raise UserError("Írj legalább egy jelenetet (soronként egyet).")
        if len(scenes) > MAX_STORY_SCENES:
            raise UserError(f"Legfeljebb {MAX_STORY_SCENES} jelenet lehet egy mesében.")
        left = DAILY_LIMIT - used_today(uid)
        if len(scenes) > left:
            raise UserError(f"Ehhez {len(scenes)} kép kell, de ma már csak {max(0, left)} generálható.", 429)
        seconds, crossfade = video_timing(request.form)
        size = video_size(request.form)
        ratio = "9:16" if request.form.get("orientation") == "portrait" else "16:9"
        title = request.form.get("title", "").strip()[:80] or f"Mese ({len(scenes)} jelenet)"
        music_path = save_music(request.files)
    except UserError as exc:
        if music_path:
            remove_file(music_path)
        return jsonify(error=str(exc)), exc.status

    def work(conn, progress):
        paths, captions = [], []
        for i, (scene, caption) in enumerate(scenes):
            progress(i / (len(scenes) + 1), f"Kép készítése: {i + 1} / {len(scenes)}")
            prompt = f"{style}\n{scene}" if style else scene
            row, _ = generate_image(conn, uid, prompt, ratio)
            paths.append(os.path.join(IMAGE_DIR, row["filename"]))
            captions.append(caption)
        progress(len(scenes) / (len(scenes) + 1), "Videó összefűzése...")
        filename = make_slideshow(paths, music_path, seconds, crossfade, size,
                                  captions if any(captions) else [])
        return insert_video(conn, uid, filename, len(paths), title)

    try:
        job_id = start_job(uid, "story", work, cleanup=(lambda: remove_file(music_path)) if music_path else None)
    except UserError as exc:
        if music_path:
            remove_file(music_path)
        return jsonify(error=str(exc)), exc.status
    return jsonify(job_id=job_id)


# ---------------------------------------------------------------- AI-videó (OpenAI Sora)

def sora_error(resp):
    hints = {
        400: "Az OpenAI elutasította a kérést (pl. a leírás nem felel meg a tartalmi szabályoknak).",
        401: "Az OpenAI API-kulcs hibás vagy visszavont.",
        403: "Az OpenAI fiókodnak nincs hozzáférése a Sora videómodellhez (lehet, hogy szervezeti azonosítás kell).",
        404: "A Sora videómodell nem érhető el ezzel a fiókkal.",
        429: "Túl sok kérés, vagy elfogyott a keret/kredit az OpenAI-nál.",
    }
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except ValueError:
        detail = resp.text[:300]
    app.logger.warning("Sora hiba %s: %s", resp.status_code, detail)
    base = hints.get(resp.status_code, f"Az OpenAI hibát jelzett ({resp.status_code}).")
    return UserError(f"{base} {detail}".strip(), 502)


def sora_generate(prompt, seconds, size, key, progress, out_path):
    headers = {"Authorization": f"Bearer {key}"}
    fields = {"model": SORA_MODEL, "prompt": prompt, "seconds": str(seconds), "size": size}
    resp = requests.post(OPENAI_VIDEOS_URL, headers=headers, timeout=60,
                         files={k: (None, v) for k, v in fields.items()})
    if resp.status_code >= 300:
        raise sora_error(resp)
    job = resp.json()
    deadline = time.time() + 30 * 60
    while job.get("status") in ("queued", "in_progress"):
        pct = float(job.get("progress") or 0)
        progress(pct / 100 * 0.95, "Sorban áll az OpenAI-nál..." if job["status"] == "queued"
                 else f"Az AI készíti a videót... {pct:.0f}%")
        if time.time() > deadline:
            raise UserError("Az AI-videó 30 perc alatt sem készült el.", 504)
        time.sleep(8)
        resp = requests.get(f"{OPENAI_VIDEOS_URL}/{job['id']}", headers=headers, timeout=60)
        if resp.status_code >= 300:
            raise sora_error(resp)
        job = resp.json()
    if job.get("status") != "completed":
        err = job.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise UserError(f"Az AI-videó nem készült el: {msg or job.get('status')}", 502)
    progress(0.97, "Videó letöltése...")
    with requests.get(f"{OPENAI_VIDEOS_URL}/{job['id']}/content", headers=headers,
                      timeout=300, stream=True) as resp:
        if resp.status_code >= 300:
            raise sora_error(resp)
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(1 << 20):
                f.write(chunk)


@app.post("/api/ai-video")
@login_required
def api_ai_video():
    uid = g.user["id"]
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt", "")).strip()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    try:
        if not key:
            raise UserError("AI-videóhoz OpenAI API-kulcs kell (OPENAI_API_KEY a .env fájlban).", 400)
        if not prompt:
            raise UserError("Írd le, milyen videót szeretnél.")
        if len(prompt) > 2000:
            raise UserError("A leírás legfeljebb 2000 karakter lehet.")
        seconds = int(data.get("seconds", 8))
        if seconds not in (4, 8, 12):
            seconds = 8
        size = "720x1280" if data.get("orientation") == "portrait" else "1280x720"
        since = time.time() - 24 * 3600
        used = db().execute("select count(*) from jobs where user_id = ? and kind = 'sora' "
                            "and status != 'error' and created_at >= ?", (uid, since)).fetchone()[0]
        if used >= AI_VIDEO_DAILY_LIMIT:
            raise UserError(f"Elérted a napi AI-videó korlátot ({AI_VIDEO_DAILY_LIMIT} / 24 óra).", 429)

        def work(conn, progress):
            filename = f"{uuid.uuid4().hex}.mp4"
            out_path = os.path.join(VIDEO_DIR, filename)
            try:
                sora_generate(prompt, seconds, size, key, progress, out_path)
            except BaseException:
                remove_file(out_path)
                raise
            title = "AI-videó: " + (prompt[:60] + "..." if len(prompt) > 60 else prompt)
            return insert_video(conn, uid, filename, 0, title)

        job_id = start_job(uid, "sora", work)
    except UserError as exc:
        return jsonify(error=str(exc)), exc.status
    return jsonify(job_id=job_id)


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
