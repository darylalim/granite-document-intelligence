"""The models directory: fill it once (`gdi convert-model`), check it before every run.

Every Docling stage loads from one `artifacts_path`, where Docling ignores
revisions (S2), so pins are enforced when the directory is *filled* and recorded
in `manifest.json`; a run compares that manifest with `config` before any model
loads (§3.13 rule 1). The 8-bit Granite Vision conversion has the source repo's
Python code stripped out, because mlx-vlm defaults `trust_remote_code` to True on
every load and Docling cannot pass False (S10, §3.13 rule 2).
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from granite_document_intelligence import config

FIX_COMMAND = "gdi convert-model"


class ModelsNotReadyError(RuntimeError):
    """The models directory cannot be trusted as it is; the message says how to fix it."""


def _not_ready(reason: str) -> ModelsNotReadyError:
    return ModelsNotReadyError(f"{reason}\nRun `{FIX_COMMAND}` to (re)build it.")


# --- Stripping the repo's code (S10) ------------------------------------------


def _drop_auto_map(value: Any, path: str, found: list[str]) -> Any:
    """`value` without any `auto_map` key at any depth; each removal recorded."""
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            here = f"{path}.{key}" if path else key
            if key == "auto_map":
                found.append(here)
                continue
            kept[key] = _drop_auto_map(item, here, found)
        return kept
    if isinstance(value, list):
        return [
            _drop_auto_map(item, f"{path}[{i}]", found) for i, item in enumerate(value)
        ]
    return value


def strip_repo_code(folder: Path) -> dict[str, list[str]]:
    """Delete every `*.py` file and every `auto_map` key under `folder`.

    With nothing to import, no loader can run the repo's code whatever its
    `trust_remote_code` default. Returns what was removed, for the manifest.
    """
    files = sorted(str(p.relative_to(folder)) for p in folder.rglob("*.py"))
    for name in files:
        (folder / name).unlink()
    auto_maps: list[str] = []
    for path in sorted(folder.rglob("*.json")):
        found: list[str] = []
        data = json.loads(path.read_text(encoding="utf-8"))
        stripped = _drop_auto_map(data, "", found)
        if found:
            path.write_text(json.dumps(stripped, indent=2) + "\n", encoding="utf-8")
            rel = path.relative_to(folder)
            auto_maps.extend(f"{rel}:{key}" for key in found)
    return {"files": files, "auto_map": auto_maps}


def repo_code_in(folder: Path) -> list[str]:
    """Every `*.py` file and `auto_map` key under `folder`, as `relative[:key]`."""
    found = [str(p.relative_to(folder)) for p in sorted(folder.rglob("*.py"))]
    for path in sorted(folder.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Not JSON we could read — say so rather than vouch for it.
            found.append(f"{path.relative_to(folder)}:unreadable")
            continue
        keys: list[str] = []
        _drop_auto_map(data, "", keys)
        found.extend(f"{path.relative_to(folder)}:{key}" for key in keys)
    return found


# --- Checking the directory before a run --------------------------------------


def verify_models_dir(models_dir: Path) -> dict[str, Any]:
    """Return the manifest, or raise naming the fix; loads no model.

    Refuses: a missing or unreadable manifest; a pinned revision, conversion
    source, or quantization that disagrees with `config`; a missing folder or one
    that is a symlink (a link into the HF cache breaks when that cache is
    cleaned, §3.13 rule 1); and any `*.py` file or `auto_map` key anywhere.
    """
    manifest_path = models_dir / config.MANIFEST_NAME
    if not manifest_path.is_file():
        raise _not_ready(f"No models manifest at {manifest_path}.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fetched = manifest["models"]
        conversion = manifest["conversion"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _not_ready(
            f"Unreadable models manifest at {manifest_path}: {exc}"
        ) from exc

    for model in config.FETCHED_MODELS:
        entry = fetched.get(model.repo_id) if isinstance(fetched, dict) else None
        if not isinstance(entry, dict) or entry.get("revision") != model.revision:
            got = entry.get("revision") if isinstance(entry, dict) else None
            raise _not_ready(
                f"{model.repo_id} is at {got or 'no recorded revision'}, "
                f"but this version pins {model.revision}."
            )
    source = config.GRANITE_VISION_SOURCE
    expected = {
        "source": source.repo_id,
        "source_revision": source.revision,
        "q_bits": config.GRANITE_VISION_Q_BITS,
    }
    for key, want in expected.items():
        if not isinstance(conversion, dict) or conversion.get(key) != want:
            got = conversion.get(key) if isinstance(conversion, dict) else None
            raise _not_ready(
                f"The Granite Vision conversion records {key}={got!r}, "
                f"but this version expects {want!r}."
            )

    folders = [m.folder for m in config.FETCHED_MODELS] + [config.GRANITE_VISION_FOLDER]
    for name in folders:
        folder = models_dir / name
        if folder.is_symlink():
            raise _not_ready(f"{folder} is a symlink; the models must be real files.")
        if not folder.is_dir():
            raise _not_ready(f"Missing model folder {folder}.")
    # Each model folder, not the directory: the manifest itself names the
    # `auto_map` keys it records as stripped.
    code = [
        f"{name}/{entry}"
        for name in folders
        for entry in repo_code_in(models_dir / name)
    ]
    if code:
        raise _not_ready(
            "The models directory holds model-repo code, which a loader could run: "
            + ", ".join(code)
        )
    return manifest


# --- Filling the directory ----------------------------------------------------


def _write_json_atomically(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _hf_cached_source() -> tuple[int, list[str]]:
    """Bytes the bf16 source holds in the HF cache, and its cached revision hashes."""
    from huggingface_hub import scan_cache_dir

    for repo in scan_cache_dir().repos:
        if (
            repo.repo_type == "model"
            and repo.repo_id == config.GRANITE_VISION_SOURCE.repo_id
        ):
            return repo.size_on_disk, [r.commit_hash for r in repo.revisions]
    return 0, []


def _delete_hf_source(revisions: list[str]) -> None:
    from huggingface_hub import scan_cache_dir

    scan_cache_dir().delete_revisions(*revisions).execute()


def _load_test(folder: Path) -> None:
    """Load the conversion the way Phase B will, then release it."""
    from mlx_vlm import load

    from granite_document_intelligence import memory

    model, processor = load(str(folder), trust_remote_code=False)
    del model, processor
    memory.release()


def _convert_granite_vision(destination: Path) -> None:
    # The function, unlike mlx-vlm's CLI, defaults trust_remote_code to True and
    # imported the repo's configuration.py when left to it (S10).
    from mlx_vlm.convert import convert

    source = config.GRANITE_VISION_SOURCE
    convert(
        hf_path=source.repo_id,
        mlx_path=str(destination),
        quantize=True,
        q_bits=config.GRANITE_VISION_Q_BITS,
        q_group_size=config.GRANITE_VISION_Q_GROUP_SIZE,
        revision=source.revision,
        trust_remote_code=False,
    )


def _fetch(repo_id: str, revision: str, destination: Path) -> None:
    from huggingface_hub import snapshot_download

    # Real files (local_dir), not symlinks into the HF cache (§3.13 rule 1).
    snapshot_download(repo_id, revision=revision, local_dir=destination)


# The chunking tokenizer's files: no weights, and no repo code to run (S10).
TOKENIZER_FILES = (
    "config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def _fetch_tokenizer() -> None:
    """Cache the chunking tokenizer at its pin, where `chunk.load_tokenizer` reads it.

    It stays in the Hugging Face cache rather than the models directory: it is
    loaded by transformers, not Docling, so `artifacts_path` never applies.
    """
    from huggingface_hub import snapshot_download

    pinned = config.EMBEDDING_TOKENIZER
    snapshot_download(
        pinned.repo_id, revision=pinned.revision, allow_patterns=list(TOKENIZER_FILES)
    )


def convert_model(
    cache_dir: Path | None = None,
    *,
    delete_source: bool = False,
    log: Callable[[str], None] = print,
) -> Path:
    """Fill `<cache_dir>/models/` and return it (§6 "Model acquisition").

    Fetches the two Docling models and the chunking tokenizer at their pins, converts Granite Vision to
    8-bit with `trust_remote_code=False`, strips the repo's code, load-tests the
    result and writes `manifest.json` last — its presence is what says the
    directory is complete. The bf16 source is deleted from the HF cache only with
    `delete_source`, and only after the load test passed (§3.12 rule 3).
    """
    models_dir = (cache_dir or config.cache_dir()) / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = models_dir / config.MANIFEST_NAME
    # An interrupted rebuild must not leave an old manifest vouching for new folders.
    manifest_path.unlink(missing_ok=True)

    fetched: dict[str, dict[str, str]] = {}
    for model in config.FETCHED_MODELS:
        log(f"Fetching {model.repo_id} at {model.revision[:12]}…")
        folder = models_dir / model.folder
        if folder.is_symlink():
            folder.unlink()
        _fetch(model.repo_id, model.revision, folder)
        stripped = strip_repo_code(folder)
        if stripped["files"] or stripped["auto_map"]:
            log(f"  stripped repo code from {model.folder}: {stripped}")
        fetched[model.repo_id] = {"revision": model.revision, "folder": model.folder}

    tokenizer = config.EMBEDDING_TOKENIZER
    log(f"Fetching the {tokenizer.repo_id} tokenizer at {tokenizer.revision[:12]}…")
    _fetch_tokenizer()

    source = config.GRANITE_VISION_SOURCE
    staging = models_dir / (config.GRANITE_VISION_FOLDER + ".partial")
    shutil.rmtree(staging, ignore_errors=True)
    log(
        f"Converting {source.repo_id} at {source.revision[:12]} to "
        f"{config.GRANITE_VISION_Q_BITS}-bit MLX (downloads the 8 GB bf16 source once)…"
    )
    _convert_granite_vision(staging)
    stripped = strip_repo_code(staging)
    log(
        f"  stripped repo code: {len(stripped['files'])} files, {len(stripped['auto_map'])} auto_map keys"
    )
    log("Load-testing the conversion…")
    _load_test(staging)
    final = models_dir / config.GRANITE_VISION_FOLDER
    if final.is_symlink() or final.is_file():
        final.unlink()
    elif final.exists():
        shutil.rmtree(final)
    os.replace(staging, final)

    from importlib.metadata import version

    manifest = {
        "models": fetched,
        "conversion": {
            "source": source.repo_id,
            "source_revision": source.revision,
            "mlx_vlm": version("mlx-vlm"),
            "q_bits": config.GRANITE_VISION_Q_BITS,
            "q_group_size": config.GRANITE_VISION_Q_GROUP_SIZE,
            "folder": config.GRANITE_VISION_FOLDER,
            "stripped": stripped,
        },
    }
    _write_json_atomically(manifest_path, manifest)
    verify_models_dir(models_dir)
    log(f"Models ready in {models_dir}")

    size, revisions = _hf_cached_source()
    if revisions:
        if delete_source:
            _delete_hf_source(revisions)
            log(
                f"Deleted the bf16 source from the Hugging Face cache ({size / 1e9:.1f} GB)."
            )
        else:
            log(
                f"The bf16 source still takes {size / 1e9:.1f} GB in the Hugging Face cache "
                "and is only needed to convert again. Remove it with:\n"
                f"  hf cache rm model/{source.repo_id}"
            )
    return models_dir
