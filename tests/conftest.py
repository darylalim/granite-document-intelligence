"""Shared fixtures: a hand-built DoclingDocument and a blank PDF, no models.

docling-core and pypdfium2 are imported inside the fixtures, never at module
level, so collecting a test file that does not use them stays cheap (§3.14 rule 9).
"""

from pathlib import Path
from typing import Any

import pytest

PAGE_W, PAGE_H = 612.0, 792.0
# A chart's box on page 1, top-left origin, in PDF points.
CHART_BOX = (100.0, 200.0, 340.0, 380.0)
LOGO_BOX = (36.0, 40.0, 106.0, 76.0)


def _classified(kind: str, confidence: float) -> Any:
    from docling_core.types.doc import (
        PictureClassificationMetaField,
        PictureClassificationPrediction,
        PictureMeta,
    )

    return PictureMeta(
        classification=PictureClassificationMetaField(
            predictions=[
                PictureClassificationPrediction(class_name=kind, confidence=confidence)
            ]
        )
    )


def build_document(*, charts: int = 1) -> Any:
    """One page: a heading, a paragraph, `charts` bar charts, a logo and a 2×2 table."""
    from docling_core.types.doc import (
        BoundingBox,
        CoordOrigin,
        DocItemLabel,
        DoclingDocument,
        ImageRef,
        ProvenanceItem,
        Size,
        TableCell,
        TableData,
    )
    from PIL import Image

    def prov(box: tuple[float, float, float, float]) -> ProvenanceItem:
        l, t, r, b = box
        return ProvenanceItem(
            page_no=1,
            bbox=BoundingBox(l=l, t=t, r=r, b=b, coord_origin=CoordOrigin.TOPLEFT),
            charspan=(0, 0),
        )

    doc = DoclingDocument(name="sample")
    page_image = Image.new("RGB", (int(PAGE_W), int(PAGE_H)), "white")
    doc.add_page(
        page_no=1,
        size=Size(width=PAGE_W, height=PAGE_H),
        image=ImageRef.from_pil(page_image, dpi=72),
    )
    doc.add_heading("Quarterly report", prov=prov((36, 90, 300, 110)))
    doc.add_text(
        DocItemLabel.TEXT,
        "Revenue rose in every region.",
        prov=prov((36, 120, 500, 140)),
    )
    for _ in range(charts):
        chart = doc.add_picture(
            prov=prov(CHART_BOX),
            image=ImageRef.from_pil(Image.new("RGB", (240, 180), "blue"), dpi=72),
        )
        chart.meta = _classified("bar_chart", 0.97)
    logo = doc.add_picture(
        prov=prov(LOGO_BOX),
        image=ImageRef.from_pil(Image.new("RGB", (70, 36), "red"), dpi=72),
    )
    logo.meta = _classified("logo", 0.95)
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
            column_header=r == 0,
        )
        for r, row in enumerate([["Region", "Revenue"], ["North", "120"]])
        for c, text in enumerate(row)
    ]
    doc.add_table(
        data=TableData(table_cells=cells, num_rows=2, num_cols=2),
        prov=prov((36, 420, 400, 480)),
    )
    return doc


@pytest.fixture
def blank_pdf(tmp_path: Path) -> Path:
    """A one-page US-letter PDF with nothing drawn on it."""
    import pypdfium2

    pdf = pypdfium2.PdfDocument.new()
    pdf.new_page(PAGE_W, PAGE_H)
    path = tmp_path / "blank.pdf"
    pdf.save(path)
    pdf.close()
    return path
