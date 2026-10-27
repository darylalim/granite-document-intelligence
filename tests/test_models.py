"""`gdi convert-model`'s directory and the check every run makes of it (§3.13 rules 1–2).

No model is fetched, converted or loaded: the Hub, mlx-vlm and the HF cache are
replaced by fakes that build the files the real calls would.
"""

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from granite_document_intelligence import config, models
from granite_document_intelligence.models import ModelsNotReadyError

# What mlx-vlm's conversion of granite-vision-4.1-4b writes beside the weights:
# the repo's four Python files, and `auto_map` at the top level of two configs
# and nested under `image_processor` in a third (S10).
REPO_FILES = ("configuration.py", "downsampling.py", "modeling.py", "processing.py")


def _mlx_vlm_output(folder: Path) -> None:
    folder.mkdir(parents=True)
    for name in REPO_FILES:
        (folder / name).write_text("raise SystemExit('repo code ran')\n")
    (folder / "config.json").write_text(
        json.dumps({"model_type": "granite4_vision", "auto_map": {"AutoConfig": "x.Y"}})
    )
    (folder / "preprocessor_config.json").write_text(
        json.dumps({"auto_map": {"a": "b"}})
    )
    (folder / "processor_config.json").write_text(
        json.dumps({"image_processor": {"size": 384, "auto_map": {"a": "b"}}})
    )
    (folder / "tokenizer.json").write_text(json.dumps({"model": {"vocab": {}}}))
    (folder / "model.safetensors").write_bytes(b"weights")


def _ready_dir(root: Path) -> Path:
    """A models directory that passes `verify_models_dir`."""
    root.mkdir(parents=True, exist_ok=True)
    for model in config.FETCHED_MODELS:
        (root / model.folder).mkdir()
        (root / model.folder / "config.json").write_text("{}")
    (root / config.GRANITE_VISION_FOLDER).mkdir()
    (root / config.GRANITE_VISION_FOLDER / "config.json").write_text("{}")
    manifest = {
        "models": {
            m.repo_id: {"revision": m.revision, "folder": m.folder}
            for m in config.FETCHED_MODELS
        },
        "conversion": {
            "source": config.GRANITE_VISION_SOURCE.repo_id,
            "source_revision": config.GRANITE_VISION_SOURCE.revision,
            "q_bits": config.GRANITE_VISION_Q_BITS,
        },
    }
    (root / config.MANIFEST_NAME).write_text(json.dumps(manifest))
    return root


def _no_repo_code_anywhere(folder: Path) -> None:
    assert not list(folder.rglob("*.py"))
    for path in folder.rglob("*.json"):
        assert "auto_map" not in path.read_text(), path


class TestStripRepoCode:
    def test_leaves_no_python_and_no_auto_map_at_any_depth(
        self, tmp_path: Path
    ) -> None:
        folder = tmp_path / "gv"
        _mlx_vlm_output(folder)
        models.strip_repo_code(folder)
        _no_repo_code_anywhere(folder)

    def test_records_what_it_removed(self, tmp_path: Path) -> None:
        folder = tmp_path / "gv"
        _mlx_vlm_output(folder)
        stripped = models.strip_repo_code(folder)
        assert stripped["files"] == sorted(REPO_FILES)
        assert sorted(stripped["auto_map"]) == [
            "config.json:auto_map",
            "preprocessor_config.json:auto_map",
            "processor_config.json:image_processor.auto_map",
        ]

    def test_keeps_everything_else(self, tmp_path: Path) -> None:
        folder = tmp_path / "gv"
        _mlx_vlm_output(folder)
        models.strip_repo_code(folder)
        assert json.loads((folder / "config.json").read_text()) == {
            "model_type": "granite4_vision"
        }
        assert json.loads((folder / "processor_config.json").read_text()) == {
            "image_processor": {"size": 384}
        }
        assert (folder / "model.safetensors").read_bytes() == b"weights"


class TestVerifyModelsDir:
    def test_a_complete_directory_passes(self, tmp_path: Path) -> None:
        manifest = models.verify_models_dir(_ready_dir(tmp_path / "models"))
        assert manifest["conversion"]["q_bits"] == config.GRANITE_VISION_Q_BITS

    def _refused(self, root: Path) -> str:
        with pytest.raises(ModelsNotReadyError) as info:
            models.verify_models_dir(root)
        message = str(info.value)
        assert "gdi convert-model" in message
        return message

    def test_a_missing_manifest_is_refused(self, tmp_path: Path) -> None:
        self._refused(tmp_path / "nothing-here")

    def test_an_unreadable_manifest_is_refused(self, tmp_path: Path) -> None:
        root = _ready_dir(tmp_path / "models")
        (root / config.MANIFEST_NAME).write_text("{not json")
        self._refused(root)

    @pytest.mark.parametrize(
        "model", config.FETCHED_MODELS, ids=[m.folder for m in config.FETCHED_MODELS]
    )
    def test_a_fetched_model_at_another_revision_is_refused(
        self, tmp_path: Path, model: config.PinnedModel
    ) -> None:
        root = _ready_dir(tmp_path / "models")
        manifest = json.loads((root / config.MANIFEST_NAME).read_text())
        manifest["models"][model.repo_id]["revision"] = "main"
        (root / config.MANIFEST_NAME).write_text(json.dumps(manifest))
        assert model.revision in self._refused(root)

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            pytest.param("source_revision", "37d591f", id="source-revision"),
            pytest.param("source", "someone/else", id="source-repo"),
            pytest.param("q_bits", 4, id="q-bits"),
        ],
    )
    def test_a_conversion_that_disagrees_with_config_is_refused(
        self, tmp_path: Path, key: str, value: object
    ) -> None:
        root = _ready_dir(tmp_path / "models")
        manifest = json.loads((root / config.MANIFEST_NAME).read_text())
        manifest["conversion"][key] = value
        (root / config.MANIFEST_NAME).write_text(json.dumps(manifest))
        self._refused(root)

    def test_a_python_file_in_any_folder_is_refused(self, tmp_path: Path) -> None:
        root = _ready_dir(tmp_path / "models")
        (root / config.GRANITE_DOCLING.folder / "sub").mkdir()
        (root / config.GRANITE_DOCLING.folder / "sub" / "modeling.py").write_text("")
        assert "modeling.py" in self._refused(root)

    def test_a_nested_auto_map_is_refused(self, tmp_path: Path) -> None:
        root = _ready_dir(tmp_path / "models")
        (root / config.GRANITE_VISION_FOLDER / "processor_config.json").write_text(
            json.dumps({"image_processor": {"auto_map": {"a": "b"}}})
        )
        assert "image_processor.auto_map" in self._refused(root)

    def test_a_missing_folder_is_refused(self, tmp_path: Path) -> None:
        root = _ready_dir(tmp_path / "models")
        (root / config.GRANITE_VISION_FOLDER / "config.json").unlink()
        (root / config.GRANITE_VISION_FOLDER).rmdir()
        self._refused(root)

    def test_a_symlinked_folder_is_refused(self, tmp_path: Path) -> None:
        # A link into the HF cache breaks when that cache is cleaned (§3.13 rule 1).
        root = _ready_dir(tmp_path / "models")
        target = tmp_path / "hf-cache-snapshot"
        (root / config.GRANITE_DOCLING.folder).rename(target)
        (root / config.GRANITE_DOCLING.folder).symlink_to(target)
        assert "symlink" in self._refused(root)


class _FakeBackends:
    """Stand-ins for the Hub, mlx-vlm and the HF cache, recording the order of calls."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, load_fails: bool = False
    ) -> None:
        self.events: list[str] = []
        self.convert_kwargs: dict[str, object] = {}
        self.load_fails = load_fails
        monkeypatch.setattr(models, "_fetch", self.fetch)
        monkeypatch.setattr(
            models, "_fetch_tokenizer", lambda: self.events.append("fetch-tokenizer")
        )
        monkeypatch.setattr(models, "_load_test", self.load_test)
        monkeypatch.setattr(
            models, "_hf_cached_source", lambda: (8_000_000_000, ["dd48e97"])
        )
        monkeypatch.setattr(models, "_delete_hf_source", self.delete_source)
        # Fake modules rather than patching the real one: importing mlx-vlm costs
        # seconds of every gate (§3.14 rule 9).
        package = types.ModuleType("mlx_vlm")
        module = types.ModuleType("mlx_vlm.convert")
        module.convert = self.convert  # ty: ignore[unresolved-attribute]
        package.convert = module  # ty: ignore[unresolved-attribute]
        monkeypatch.setitem(sys.modules, "mlx_vlm", package)
        monkeypatch.setitem(sys.modules, "mlx_vlm.convert", module)

    def fetch(self, repo_id: str, revision: str, destination: Path) -> None:
        self.events.append(f"fetch {repo_id}@{revision}")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "config.json").write_text("{}")

    def convert(self, **kwargs: object) -> None:
        self.events.append("convert")
        self.convert_kwargs = kwargs
        _mlx_vlm_output(Path(str(kwargs["mlx_path"])))

    def load_test(self, folder: Path) -> None:
        self.events.append("load-test")
        assert not list(folder.rglob("*.py")), "load-tested before stripping"
        if self.load_fails:
            raise RuntimeError("could not load")

    def delete_source(self, revisions: list[str]) -> None:
        self.events.append(f"delete-source {revisions}")


class TestConvertModel:
    def test_caches_the_chunking_tokenizer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An offline `gdi convert` chunks from the HF cache (§3.9), so setup fills it.
        fake = _FakeBackends(monkeypatch)
        models.convert_model(tmp_path, log=lambda _: None)
        assert "fetch-tokenizer" in fake.events

    def test_the_tokenizer_is_fetched_at_its_pin_without_weights(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def snapshot_download(repo_id: str, **kwargs: Any) -> str:
            seen.update(kwargs, repo_id=repo_id)
            return ""

        monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot_download)
        models._fetch_tokenizer()
        assert seen["repo_id"] == config.EMBEDDING_TOKENIZER.repo_id
        assert seen["revision"] == config.EMBEDDING_TOKENIZER.revision
        assert "tokenizer.json" in seen["allow_patterns"]
        assert not any(
            p.endswith((".safetensors", ".bin", ".py")) for p in seen["allow_patterns"]
        )

    def test_converts_without_trusting_remote_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _FakeBackends(monkeypatch)
        models.convert_model(tmp_path, log=lambda _: None)
        assert fakes.convert_kwargs["trust_remote_code"] is False
        assert fakes.convert_kwargs["revision"] == config.GRANITE_VISION_SOURCE.revision
        assert fakes.convert_kwargs["q_bits"] == config.GRANITE_VISION_Q_BITS
        assert fakes.convert_kwargs["quantize"] is True

    def test_fetches_each_docling_model_at_its_pin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _FakeBackends(monkeypatch)
        models.convert_model(tmp_path, log=lambda _: None)
        for model in config.FETCHED_MODELS:
            assert f"fetch {model.repo_id}@{model.revision}" in fakes.events

    def test_produces_a_directory_the_run_check_accepts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeBackends(monkeypatch)
        models_dir = models.convert_model(tmp_path, log=lambda _: None)
        for name in [m.folder for m in config.FETCHED_MODELS] + [
            config.GRANITE_VISION_FOLDER
        ]:
            _no_repo_code_anywhere(models_dir / name)
        manifest = models.verify_models_dir(models_dir)
        stripped = manifest["conversion"]["stripped"]
        assert stripped["files"] == sorted(REPO_FILES)
        assert len(stripped["auto_map"]) == 3
        assert not (models_dir / (config.GRANITE_VISION_FOLDER + ".partial")).exists()

    def test_keeps_the_source_without_the_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _FakeBackends(monkeypatch)
        lines: list[str] = []
        models.convert_model(tmp_path, log=lines.append)
        assert not any(e.startswith("delete-source") for e in fakes.events)
        assert any(
            "hf cache rm model/ibm-granite/granite-vision-4.1-4b" in line
            for line in lines
        )

    def test_deletes_the_source_only_after_the_load_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _FakeBackends(monkeypatch)
        models.convert_model(tmp_path, delete_source=True, log=lambda _: None)
        deletes = [
            i for i, e in enumerate(fakes.events) if e.startswith("delete-source")
        ]
        assert deletes == [len(fakes.events) - 1]
        assert fakes.events.index("load-test") < deletes[0]

    def test_a_failed_load_test_deletes_nothing_and_writes_no_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _FakeBackends(monkeypatch, load_fails=True)
        with pytest.raises(RuntimeError, match="could not load"):
            models.convert_model(tmp_path, delete_source=True, log=lambda _: None)
        assert not any(e.startswith("delete-source") for e in fakes.events)
        assert not (tmp_path / "models" / config.MANIFEST_NAME).exists()

    def test_a_rebuild_removes_the_old_manifest_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An interrupted rebuild must not leave an old manifest vouching for new folders.
        _ready_dir(tmp_path / "models")
        _FakeBackends(monkeypatch, load_fails=True)
        with pytest.raises(RuntimeError):
            models.convert_model(tmp_path, log=lambda _: None)
        assert not (tmp_path / "models" / config.MANIFEST_NAME).exists()
