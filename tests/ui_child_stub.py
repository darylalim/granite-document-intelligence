"""A stand-in for `gdi convert`, run by `runner.start` in tests (plan §18.5).

One committed file, driven by `$STUB_CHILD_*` variables, never a file per test
(CLAUDE.md, Tests). Standard library only, so it starts in milliseconds.

- `STUB_CHILD_EVENTS`: lines appended to the `--events` file first.
- `STUB_CHILD_STDERR`: text written to stderr.
- `STUB_CHILD_HOLD`: seconds to wait afterwards. A SIGINT while waiting appends
  `interrupted` and exits 130, as the CLI's Ctrl-C path does.
- `STUB_CHILD_SIGNAL`: a signal number to kill itself with, as a crash.
- `STUB_CHILD_EXIT`: the exit status otherwise (default 0).
"""

import json
import os
import signal
import sys
import time


def main(argv: list[str]) -> int:
    events = argv[argv.index("--events") + 1]

    def append(text: str) -> None:
        with open(events, "a", encoding="utf-8") as handle:
            handle.write(text)

    # The writes sit inside the try: a test sends SIGINT once it can read the
    # first event, which can be before this append has closed the file. Outside
    # the try, that KeyboardInterrupt went uncaught and the child died of SIGINT
    # (-2) instead of exiting 130 (288 of 300 runs when signalled the moment the
    # line appears; test_cancel_is_ctrl_c failed this way once in CI).
    try:
        append(os.environ.get("STUB_CHILD_EVENTS", ""))
        sys.stderr.write(os.environ.get("STUB_CHILD_STDERR", ""))
        sys.stderr.flush()
        time.sleep(float(os.environ.get("STUB_CHILD_HOLD", "0")))
    except KeyboardInterrupt:
        append(json.dumps({"v": 1, "event": "interrupted"}) + "\n")
        return 130
    if os.environ.get("STUB_CHILD_SIGNAL"):
        os.kill(os.getpid(), int(os.environ["STUB_CHILD_SIGNAL"]))
        time.sleep(5)
    return int(os.environ.get("STUB_CHILD_EXIT", "0"))


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    sys.exit(main(sys.argv))
