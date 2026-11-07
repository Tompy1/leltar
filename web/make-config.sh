#!/bin/sh
# Netlify build lépés: a Netlify környezeti változóiból elkészíti a web/config.js fájlt.
# (A SUPABASE_ANON_KEY nyilvános kulcs, a böngészőbe kerülhet. A service role kulcsot SOHA ne add meg itt!)
set -e
DIR="$(dirname "$0")"
if [ -z "$SUPABASE_URL" ] || [ -z "$SUPABASE_ANON_KEY" ]; then
  echo "HIBA: állítsd be a SUPABASE_URL és SUPABASE_ANON_KEY környezeti változókat a Netlify-on." >&2
  exit 1
fi
case "$SUPABASE_ANON_KEY" in
  sb_secret_*) echo "HIBA: ez a titkos (service role) kulcs, nem az anon/publishable kulcs!" >&2; exit 1 ;;
esac
printf 'window.APP_CONFIG = { SUPABASE_URL: "%s", SUPABASE_ANON_KEY: "%s" };\n' \
  "$SUPABASE_URL" "$SUPABASE_ANON_KEY" > "$DIR/config.js"
echo "config.js elkészült."
