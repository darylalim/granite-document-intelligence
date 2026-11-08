#!/bin/bash
# Step 1 of the demo videos (docs/plans/demo-video.md): run the real conversion
# once, so every later step replays real output and never runs a model.
#
#   demo/stage.sh            # stage, or refuse if already staged
#   demo/stage.sh --force    # convert again, replacing the staged run
#
# Writes, under the gitignored demo/build/:
#   home/                 the scratch HOME the UI shots run under, so it shows ~/gdi-out
#     gao-07-631.pdf, schema.json   the inputs, copied in
#     gdi-out/gao-07-631/           the §3.5 tree
#   transcript.jsonl      every output line with its time (demo/timestamp.py)
#   events.jsonl          the --events stream
#   stage.json            the command, wall time, commit and date, for the
#                         "the real run took N min" caption
#
# The command runs from the scratch HOME with relative paths, so the replayed
# terminal output and the UI's Run view (which prints the --schema path as
# recorded) show `schema.json` and `gdi-out`, never this Mac's home directory.
#
# Run it on an idle Mac on AC power, as with the eval: the wall time goes on screen.

set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd -- "$root"

force=0
case "${1-}" in
'') ;;
--force) force=1 ;;
*)
	echo "usage: demo/stage.sh [--force]" >&2
	exit 2
	;;
esac

build=demo/build
home="$root/$build/home"
tree="$home/gdi-out/gao-07-631"
pdf=eval/corpus/gao-07-631.pdf
schema=eval/expected/gao-07-631/kvp/schema.json

if [ -f "$tree/run.json" ] && [ "$force" -eq 0 ]; then
	echo "stage: already staged at $tree; pass --force to convert again" >&2
	exit 1
fi

# The scratch HOME would otherwise move every cache the run needs: the models
# directory, the Hugging Face cache holding the chunking tokenizer, and uv's.
# Pin them to the real ones, and call the venv's gdi rather than `uv run`.
real_home=$HOME
export GDI_CACHE_DIR="${GDI_CACHE_DIR:-$real_home/.cache/granite-document-intelligence}"
export HF_HOME="${HF_HOME:-$real_home/.cache/huggingface}"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

uv sync --locked --quiet
gdi="$root/.venv/bin/gdi"
python="$root/.venv/bin/python"

# The models must be present and match the pins. convert() checks this too, but
# only after the run has started; fail before anything is written instead.
if ! "$python" -I -c '
from granite_document_intelligence import config, models
models.verify_models_dir(config.models_dir())
'; then
	echo "stage: fetching the models (about 3 min plus a ~9 GB download)" >&2
	HF_HUB_OFFLINE=0 "$gdi" convert-model
fi

if ! pmset -g batt | grep -q "AC Power"; then
	echo "stage: warning: on battery; the wall time shown on screen may be slow" >&2
fi

mkdir -p "$home"
cp "$pdf" "$home/gao-07-631.pdf"
cp "$schema" "$home/schema.json"
rm -f "$build/events.jsonl" "$build/transcript.jsonl" "$build/stage.json"

# Exactly what the terminal shot types.
cmd=(gdi convert gao-07-631.pdf --schema schema.json -o gdi-out)
extra=(--events "$root/$build/events.jsonl")
[ "$force" -eq 1 ] && extra+=(--force)

start=$(date +%s)
set +e
(
	cd -- "$home"
	HOME="$home" PATH="$root/.venv/bin:$PATH" "${cmd[@]}" "${extra[@]}" 2>&1
) | "$python" -I demo/timestamp.py "$build/transcript.jsonl"
status=${PIPESTATUS[0]}
set -e
end=$(date +%s)

if [ "$status" -ne 0 ]; then
	echo "stage: gdi convert exited $status; nothing recorded as staged" >&2
	exit 1
fi

"$python" -I - "$build/stage.json" "$((end - start))" "${cmd[*]}" <<'EOF'
import datetime, json, subprocess, sys

path, seconds, command = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sha = subprocess.run(
    ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
).stdout.strip()
record = {
    "command": command,
    "wall_seconds": seconds,
    "commit": sha,
    "date": datetime.date.today().isoformat(),
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(record, f, indent=2)
    f.write("\n")
EOF

echo "stage: done in $((end - start)) s; tree at $tree" >&2
