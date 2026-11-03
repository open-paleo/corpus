# PDF.js (vendored)

`pdfjs-dist` 6.4.299 from the npm registry (tarball integrity checked against
the registry's sha512), Apache-2.0 (`LICENSE`). Only what `corpus web` loads
at run time is kept:

- `pdf.min.mjs`, `pdf.worker.min.mjs`: the library and its worker
- `cmaps/`: character maps, for CJK fonts
- `standard_fonts/`: the 14 standard PDF fonts, for PDFs that don't embed them
- `wasm/`: decoders for JPEG 2000 (OpenJPEG, BSD-2), JBIG2 (PDFium, BSD-3) and
  colour profiles (qcms, MIT); their licences are beside them
- `iccs/`: a CMYK colour profile (CC0)

Left out: the viewer application, the JavaScript sandbox, the decoders'
non-WebAssembly fallbacks, the legacy build and the source maps.

To upgrade, replace these files from the new release's package and update the
version here.
