"""Phase A's Docling configuration and our capped/looped page checks.

The options test is the silent-degradation guard: every wrong value here still
converts, just from the wrong model, the wrong boxes or the wrong text (§8).
Docling is imported inside the tests that need it (§3.14 rule 9).
"""

from pathlib import Path
from typing import Any

import pytest

from granite_document_intelligence import config, phase_a
from granite_document_intelligence.api import ConvertOptions

MODELS = Path("/tmp/gdi-models")


def _options(**kwargs: Any) -> Any:
    return phase_a.pipeline_options(ConvertOptions(**kwargs), MODELS)


class TestPipelineOptions:
    def test_uses_the_granite_docling_preset_on_mlx(self) -> None:
        from docling.datamodel.pipeline_options import VlmConvertOptions
        from docling.datamodel.vlm_engine_options import MlxVlmEngineOptions

        vlm = _options().vlm_options
        # The preset, not the deprecated GRANITEDOCLING_MLX spec (S3).
        assert isinstance(vlm, VlmConvertOptions)
        assert isinstance(vlm.engine_options, MlxVlmEngineOptions)
        assert vlm.model_spec.name == "Granite-Docling-258M"
        assert vlm.model_spec.max_new_tokens == 8192

    def test_resolves_the_pinned_mlx_repo_inside_the_models_directory(self) -> None:
        from docling.datamodel.vlm_engine_options import VlmEngineType

        pipe = _options()
        assert pipe.artifacts_path == MODELS
        repo = pipe.vlm_options.model_spec.get_repo_id(VlmEngineType.MLX)
        # Under artifacts_path Docling loads <dir>/<repo with / → -->, which is
        # the folder `gdi convert-model` fills at the pin (S2).
        assert repo.replace("/", "--") == config.GRANITE_DOCLING.folder

    def test_the_classifier_resolves_to_the_fetched_folder(self) -> None:
        pipe = _options()
        assert pipe.do_picture_classification is True
        repo = pipe.picture_classification_options.repo_id
        assert repo.replace("/", "--") == config.FIGURE_CLASSIFIER.folder

    def test_leaves_docling_chart_extraction_off(self) -> None:
        # Granite Vision runs in our chart stage, on our crop (decided 2026-10-27).
        assert _options().do_chart_extraction is False

    def test_images_scale_is_exactly_one(self) -> None:
        # Any other value moves every box and crops every chart wrongly (S7).
        assert _options().images_scale == 1.0 == config.IMAGES_SCALE

    def test_never_calls_remote_services(self) -> None:
        assert _options().enable_remote_services is False

    def test_keeps_page_and_picture_images(self) -> None:
        pipe = _options()
        assert pipe.generate_page_images is True
        assert pipe.generate_picture_images is True

    @pytest.mark.parametrize(
        ("source", "expected"),
        [pytest.param("vlm", False, id="vlm"), pytest.param("pdf", True, id="pdf")],
    )
    def test_text_source_sets_the_flag_on_the_convert_options_only(
        self, source: str, expected: bool
    ) -> None:
        # On VlmPipelineOptions the flag is a silent no-op on the preset path (S5).
        pipe = _options(text_source=source)
        assert pipe.vlm_options.force_backend_text is expected
        assert pipe.force_backend_text is False


LOOP_BLOCK = "<text><loc_43><loc_120><loc_49><loc_131>Table</text>"
CLEAN_PAGE = (
    "<doctag><section_header_level_1><loc_40><loc_20><loc_300><loc_30>Results"
    "</section_header_level_1><text><loc_40><loc_40><loc_450><loc_90>Revenue rose."
    "</text><picture><loc_100><loc_120><loc_400><loc_300></picture></doctag>"
)


class TestCappedAndLooped:
    def test_a_clean_page_is_neither(self) -> None:
        assert not phase_a.is_capped(CLEAN_PAGE, 120, 8192)
        assert not phase_a.is_looped(CLEAN_PAGE)

    def test_a_page_at_the_cap_is_capped(self) -> None:
        # The MLX engine counts one token past the cap (S3).
        assert phase_a.is_capped(CLEAN_PAGE, 8193, 8192)
        assert phase_a.is_capped(CLEAN_PAGE, 8192, 8192)

    def test_a_page_without_its_closing_tag_is_capped(self) -> None:
        assert phase_a.is_capped(CLEAN_PAGE.removesuffix("</doctag>"), 900, 8192)

    def test_a_loop_that_closed_itself_is_looped_not_capped(self) -> None:
        # S3's sideways table: 348 identical blocks, then a clean </doctag>.
        text = "<doctag><text><loc_40><loc_20><loc_300><loc_30>Table A-2</text>"
        text += LOOP_BLOCK * 348 + "</doctag>"
        assert phase_a.is_looped(text)
        assert not phase_a.is_capped(text, 7371, 8192)

    def test_a_loop_late_in_a_long_page_is_found(self) -> None:
        prose = "".join(
            f"<text><loc_40><loc_{i}><loc_450><loc_{i + 5}>Paragraph {i}.</text>"
            for i in range(0, 400, 6)
        )
        assert not phase_a.is_looped("<doctag>" + prose + "</doctag>")
        assert phase_a.is_looped("<doctag>" + prose + LOOP_BLOCK * 4 + "</doctag>")

    def test_short_text_is_checked_too(self) -> None:
        assert phase_a.is_looped(LOOP_BLOCK * 3)
        assert not phase_a.is_looped("")


def test_profiling_settings_are_restored() -> None:
    from docling.datamodel.settings import settings

    before = settings.debug.profile_pipeline_timings, settings.perf.page_batch_size
    with phase_a._profiled():
        assert settings.debug.profile_pipeline_timings is True
        assert settings.perf.page_batch_size == 1
    assert (
        settings.debug.profile_pipeline_timings,
        settings.perf.page_batch_size,
    ) == before


def test_page_progress_reports_counts_from_doclings_public_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from docling.datamodel.progress import (
        PageCompletedProgress,
        PhaseStartedProgress,
    )

    from granite_document_intelligence.phase_a import _page_progress

    with caplog.at_level(logging.INFO, logger="granite_document_intelligence"):
        _page_progress(
            PageCompletedProgress(
                document_index=1,
                document_name="report.pdf",
                page_no=7,
                success=True,
                completed_pages=3,
                total_pages=42,
            )
        )
        _page_progress(
            PhaseStartedProgress(
                document_index=1, document_name="report.pdf", phase="build"
            )
        )
    assert [r.getMessage() for r in caplog.records] == ["  page 3/42"]
