"""Copy stdin to stdout and record each line with its time, for the terminal replay.

`stage.sh` pipes the real `gdi convert`'s output through this, and `terminal.sh`
replays the record with its timing compressed (docs/plans/demo-video.md, step 1).
Each record is one JSON object per line: `t`, seconds since this process started,
and `line`, the text without its newline. Standard library only, run with `-I`.
"""

import json
import sys
import time


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: timestamp.py RECORD.jsonl", file=sys.stderr)
        return 2
    start = time.monotonic()
    with open(sys.argv[1], "w", encoding="utf-8") as record:
        for raw in sys.stdin.buffer:
            t = time.monotonic() - start
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
            record.write(json.dumps({"t": round(t, 3), "line": line}) + "\n")
            record.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
