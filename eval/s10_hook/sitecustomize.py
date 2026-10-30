"""S10's detector: records model-repo code executed in this process (§3.13 rule 2).

`s10_check.py` puts this folder on `PYTHONPATH`, so Python loads it at startup in
every process the check starts, subprocesses included. It does nothing unless
`S10_LOG` is set. It records:

- every code object **executed** from a watched root (`S10_ROOTS`), through the
  audit `exec` event, which fires for every module body an import runs, from source
  or from a cached `.pyc`;
- every `resolve_trust_remote_code` and `get_class_from_dynamic_module` call in
  transformers, with its arguments and result;
- any `transformers_modules*` entry, or module from a watched root, left in
  `sys.modules` at exit.

At exit it appends one JSON line per process to `S10_LOG`. It imports nothing
heavy itself: transformers' hooks are wrapped only when something else imports it.
"""

import atexit
import importlib.abc
import importlib.machinery
import json
import os
import sys
from collections.abc import Callable, Sequence
from types import ModuleType
from typing import Any

TRUST_FUNCTIONS = ("resolve_trust_remote_code", "get_class_from_dynamic_module")
DYNAMIC_MODULE = "transformers.dynamic_module_utils"


def _install(log: str, roots: tuple[str, ...]) -> None:
    executed: set[str] = set()
    trust: list[str] = []

    def under(path: str) -> bool:
        try:
            return os.path.realpath(path).startswith(roots)
        except (OSError, ValueError):
            return False

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "exec":
            name = getattr(args[0], "co_filename", "")
            if name and under(name):
                executed.add(name)

    def recorded(name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            trust.append(f"{name}(args={args!r:.200}) -> {result!r:.100}")
            return result

        return wrapper

    class TrustFinder(importlib.abc.MetaPathFinder):
        """Wraps transformers' trust functions when their module is first imported."""

        def find_spec(
            self,
            fullname: str,
            path: Sequence[str] | None,
            target: ModuleType | None = None,
        ) -> importlib.machinery.ModuleSpec | None:
            if fullname != DYNAMIC_MODULE:
                return None
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is None or spec.loader is None:
                return spec
            exec_module = spec.loader.exec_module

            def exec_and_wrap(module: ModuleType) -> None:
                exec_module(module)
                for name in TRUST_FUNCTIONS:
                    if (fn := getattr(module, name, None)) is not None:
                        setattr(module, name, recorded(name, fn))

            # Patches this loader instance only; ty reads it as replacing the method.
            spec.loader.exec_module = exec_and_wrap  # ty: ignore[invalid-assignment]
            return spec

    def report() -> None:
        left = sorted(
            name
            for name, module in list(sys.modules.items())
            if name.startswith("transformers_modules")
            or under(getattr(module, "__file__", None) or "")
        )
        record = {
            "argv": sys.argv[:3],
            "executed": sorted(executed),
            "trust_decisions": trust,
            "modules_left": left,
        }
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    sys.addaudithook(audit)
    sys.meta_path.insert(0, TrustFinder())
    atexit.register(report)


if log := os.environ.get("S10_LOG"):
    _install(
        log,
        tuple(
            os.path.realpath(root) + os.sep
            for root in os.environ.get("S10_ROOTS", "").split(os.pathsep)
            if root
        ),
    )
