"""The public surface: cheap to import, validated options, refusal before any load."""

import subprocess
import sys
from pathlib import Path

import pytest

import granite_document_intelligence as gdi
from granite_document_intelligence import ConvertOptions, ModelsNotReadyError


def test_importing_the_package_loads_no_heavy_library() -> None:
    # `gdi --help` and every test pay for nothing they do not use (§6, §3.14 rule 9).
    probe = (
        "import sys, granite_document_intelligence, granite_document_intelligence.cli; "
        "print(sorted(m for m in ('docling', 'docling_core', 'torch', 'mlx', 'mlx_vlm', "
        "'transformers', 'pandas', 'PIL') if m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"


def test_the_public_names_are_exported() -> None:
    for name in (
        "convert",
        "ConvertOptions",
        "ConvertResult",
        "Chart",
        "Table",
        "RunInfo",
    ):
        assert name in gdi.__all__
        assert hasattr(gdi, name)


class TestConvertOptions:
    def test_defaults(self) -> None:
        options = ConvertOptions()
        assert options.text_source == "vlm"
        assert options.charts is True
        assert options.chart_code is False
        assert options.save_page_images is False

    def test_an_unknown_text_source_is_refused(self) -> None:
        with pytest.raises(ValueError, match="text_source"):
            ConvertOptions(text_source="ocr")  # ty: ignore[invalid-argument-type]

    def test_chart_code_needs_charts(self) -> None:
        with pytest.raises(ValueError, match="chart_code"):
            ConvertOptions(charts=False, chart_code=True)

    def test_as_dict_is_what_resume_compares(self) -> None:
        assert ConvertOptions(chart_code=True).as_dict() == {
            "text_source": "vlm",
            "charts": True,
            "chart_code": True,
            "save_page_images": False,
        }


def test_convert_refuses_an_unready_models_directory_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blank_pdf: Path
) -> None:
    monkeypatch.setenv("GDI_CACHE_DIR", str(tmp_path / "cache"))
    from granite_document_intelligence import phase_a

    def boom(*_: object) -> None:
        raise AssertionError("Phase A started without a verified models directory")

    monkeypatch.setattr(phase_a, "run", boom)
    with pytest.raises(ModelsNotReadyError, match="gdi convert-model"):
        gdi.convert(blank_pdf)
