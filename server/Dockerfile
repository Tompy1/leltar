FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    IMAGEIO_FFMPEG_EXE=/usr/bin/ffmpeg

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY image_generator/image_generator.py video_generator/video_generator.py ./
COPY server/app.py ./
COPY server/templates ./templates
COPY server/static ./static

RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["gunicorn", "--workers", "2", "--threads", "4", "--timeout", "900", \
     "--bind", "0.0.0.0:8000", "--access-logfile", "-", "app:app"]
