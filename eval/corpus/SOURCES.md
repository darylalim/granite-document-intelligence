# Corpus sources

Where each public corpus file came from, its licence and why it qualifies (plan §4.1 and
§9.1). A file whose licence cannot be stated does not go in. All files were retrieved
on **2026-10-25**. The *original* SHA-256 identifies the file as downloaded, and the
*committed* SHA-256 identifies the file in this directory, which the manifests in
`eval/expected/<stem>/manifest.json` repeat.

The excerpts were cut with `pypdf` 6.21.2 (`PdfWriter.append(..., pages=...)`, then
`remove_links()`, since the internal links pointed at pages the excerpt leaves out). That
keeps each page's content stream and text layer as published.

## gao-07-631.pdf

- **Work:** U.S. Government Accountability Office, *Defense Budget: Trends in Operation
  and Maintenance Costs and Support Services Contracting*, GAO-07-631, May 18, 2007.
  42 pages, born-digital.
- **Source:** <https://www.govinfo.gov/content/pkg/GAOREPORTS-GAO-07-631/pdf/GAOREPORTS-GAO-07-631.pdf>
  (gao.gov refuses scripted downloads with HTTP 403; govinfo mirrors the same report).
- **Licence:** public domain, a work of the U.S. government (17 U.S.C. § 105).
- **Why it qualifies:** the report's own notice (PDF page 4) says it "is not subject to
  copyright protection in the United States", and warns that a GAO work *may* contain
  copyrighted images. This one does not: all nine figures are sourced "GAO analysis of
  DOD data" or of congressional data. Its pictures are GAO logos and a GAO official's
  signature.
- **Changes:** none. SHA-256 (original and committed):
  `51f6dca94cf101b7f8dda482dffd3ba16119705083fbb06be289ee360790471f`.

## gao-07-631-scan.pdf

- **Work:** made by this project: a print-and-scan of `gao-07-631.pdf` above, PDF pages
  12–26 (plan §4.1, item 4). Those 15 pages hold all 12 plots and both tables.
- **Licence:** Apache-2.0, like the rest of the repository. The printed report is in
  the public domain (above), so the scan is the project's own work.
- **Why it qualifies:** it is the scanned multi-page case: image-only, with no text layer
  (0 characters on every page) and one image per page.
- **How it was made (2026-10-29):** the 15 pages were extracted with `pypdfium2` and
  printed at actual size, single-sided, then scanned on a Brother MFC-L2710DW at 300 DPI
  in grayscale, with no OCR. The scanner wrote 595 × 775 pt pages, cropping the Letter
  page slightly; the whole page, footer included, is inside the crop. Its document
  information is empty. Scanner specks and an edge line along the top are left in, as
  realistic noise. SHA-256
  `6c677f6b0e2df33da658b9d65bb5a0c513bc498d45053545467fc258804ae409`.

## gao-08-14-excerpt.pdf

- **Work:** U.S. Government Accountability Office, *Energy Markets: Increasing
  Globalization of Petroleum Products Markets, Tightening Refining Demand and Supply
  Balance, and Other Trends Have Implications for U.S. Energy Supply, Prices, and Price
  Volatility*, GAO-08-14, December 2007.
- **Source:** <https://www.govinfo.gov/content/pkg/GAOREPORTS-GAO-08-14/pdf/GAOREPORTS-GAO-08-14.pdf>
- **Licence:** public domain, a work of the U.S. government (17 U.S.C. § 105).
- **Why it qualifies:** the same GAO notice (PDF page 5). The excerpt's one figure,
  Figure 3 (a pie chart), is sourced "GAO analysis of Bureau of Transportation
  Statistics data".
- **Changes:** PDF pages 1 (cover) and 20 (Figure 3) of 73, extracted as described above.
  Original SHA-256 `59e4c8051d97d1136fe7d333af0120ff87f15009c58c2becbfd1a7d0d9a7a915`;
  committed `9d49cfbeece41b9e0841c31ffb4517c2e652a33f604a5116b9ee81c29e342864`.

## gao-08-1044-excerpt.pdf

- **Work:** U.S. Government Accountability Office, *Electronic Waste: EPA Needs to Better
  Control Harmful U.S. Exports through Stronger Enforcement and More Comprehensive
  Regulation*, GAO-08-1044, August 2008.
- **Source:** <https://www.govinfo.gov/content/pkg/GAOREPORTS-GAO-08-1044/pdf/GAOREPORTS-GAO-08-1044.pdf>
- **Licence:** public domain, a work of the U.S. government (17 U.S.C. § 105).
- **Why it qualifies:** the same GAO notice (PDF page 4). The two figures kept are GAO's
  own: Figure 2, a map, is sourced "GAO analysis of two Internet-e-commerce sites", and
  Figure 5, four photographs, is credited "Source: GAO." The report also carries figures
  that do **not** qualify, and the excerpt leaves them out: Figures 3 and 4 are
  photographs credited to third parties (a regional e-waste workshop and the Basel
  Convention Regional Centre in China), and Figure 1, an illustration with an inset
  photograph, carries no credit line at all.
- **Changes:** PDF pages 1 (cover), 21 (Figure 2) and 32 (Figure 5) of 67, extracted as
  described above. Original SHA-256
  `ea96d44f1b3a0d70649612a6df261111a6f5a679188f90898ba064c4564ac8a8`; committed
  `fdb8fa9c885e540c86d8af72bc7132dbcf92149da646a44a680eda76aec069bc`.

## census-p60-282-excerpt.pdf

- **Work:** Gloria Guzman and Melissa Kollar, U.S. Census Bureau, *Income in the United
  States: 2023*, Current Population Reports P60-282, September 2024.
- **Source:** <https://www2.census.gov/library/publications/2024/demo/p60-282.pdf>
- **Licence:** public domain, a work of the U.S. government (17 U.S.C. § 105).
- **Why it qualifies:** a Census Bureau report prepared by Bureau staff, sourced to the
  Bureau's own Current Population Survey. The excerpt holds only text and tables, no
  pictures.
- **Changes:** PDF pages 7 (the introduction), 22 (the first page of Table A-2, printed
  sideways), 45 and 46 (Table A-6, which runs across the page break) of 59, extracted as
  described above. Original SHA-256
  `e6cf145cb10b5d037f5e77fa199d2e558bfe24f76273f385e9767c2eabba1cbf`; committed
  `6cd5aa58f8f889cd8e5143935a9307b60dc11a01eb642be10be9c3be2a967677`.

## irs-w9-filled.pdf

- **Work:** Internal Revenue Service, Form W-9, *Request for Taxpayer Identification
  Number and Certification* (Rev. March 2024).
- **Source:** <https://www.irs.gov/pub/irs-pdf/fw9.pdf>
- **Licence:** public domain, a work of the U.S. government (17 U.S.C. § 105).
- **Why it qualifies:** an IRS form, published for anyone to fill in and redistribute.
- **Changes:** page 1 of 6 only. Filled with **fictitious** data (the values are in
  `eval/expected/irs-w9-filled/kvp/expected.json`; the SSN uses area number 000, which the
  SSA never issues), with `pypdf` 6.21.2: the XFA form was removed, the AcroForm fields
  filled and flattened into page content, and the widget annotations and AcroForm then
  removed, so the values arrive as a filled form usually does. The signature and date
  are left blank. Original SHA-256
  `2d420cbb4123dcf1fb82595b2359cfbb5d81f00b9df9d359fcc7af361d093f53`; committed
  `184b02d2ee75eee1148fcce70cd99e4a777a647ecf159de0b41988796d9e9bc4`. The IRS revises
  the form under the same URL, so a fresh download may not match the original hash.

## adversarial.pdf

- **Work:** made by this project, by `make_adversarial.py` in this directory (plan §8.2
  rule 6).
- **Licence:** Apache-2.0, like the rest of the repository.
- **Why it qualifies:** nothing in it comes from anywhere else. It uses only the PDF
  standard fonts, which are not embedded.
- **Changes:** n/a. The generator is deterministic. `uv run python
  eval/corpus/make_adversarial.py` rewrites it byte for byte: SHA-256
  `3d2a5f53ca34169fb3cedfcb99b74a13af485c2d67808b903b477f6156baad51`.
