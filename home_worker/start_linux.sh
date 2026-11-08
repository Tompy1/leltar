#!/usr/bin/env bash
# Otthoni képgeneráló worker indítása Linuxon (NVIDIA videókártyával).
set -e
cd "$(dirname "$0")"

if [ ! -f beallitasok.txt ]; then
  cp beallitasok.minta.txt beallitasok.txt
  echo "Első indítás: töltsd ki a beallitasok.txt fájlt (SZERVER és TOKEN), pl.: nano beallitasok.txt"
  echo "Utána indítsd újra: ./start_linux.sh"
  exit 0
fi

PYTHON=${PYTHON:-python3}
if [ ! -x venv/bin/python ]; then
  echo "Virtuális környezet létrehozása..."
  "$PYTHON" -m venv venv
fi
PY=venv/bin/python

if [ ! -f venv/alap.ok ]; then
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements-alap.txt
  touch venv/alap.ok
fi

if [ "$1" != "--teszt" ] && [ ! -f venv/ai.ok ]; then
  TORCH_CUDA=cu126
  if nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | grep -q "RTX 50"; then TORCH_CUDA=cu128; fi
  echo "AI-csomagok telepítése (PyTorch $TORCH_CUDA, több GB)..."
  "$PY" -m pip install torch==2.7.1 --index-url "https://download.pytorch.org/whl/$TORCH_CUDA"
  "$PY" -m pip install -r requirements-ai.txt
  touch venv/ai.ok
fi

exec "$PY" worker.py "$@"
