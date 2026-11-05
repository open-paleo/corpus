# Acquisition notes: where papers come from

Free routes to a paper, source-specific recipes, and the pitfalls of the fetch
tiers. Every route here is open access or a free public archive.

## Routes

### Free routes to try before writing a reference off
- **USGS Publications Warehouse API:** `https://pubs.usgs.gov/pubs-services/publication/{id}?format=json` or `...publication?q={title}&page_size=5`. Useful for USGS authors publishing in outside journals; it returns the author abstract verbatim. It is only an abstract and can be wrong (a missing abstract, page ranges shorter than the printed paper), so never treat it as authoritative against a document you hold.
- **BHL API:** web pages at biodiversitylibrary.org answer scripts with a Cloudflare 403, but `api3?op=GetPartMetadata&id={partid}&format=json&apikey=$BHL_API_KEY` works (the key is free) and returns title, container, volume, date and page range. Good for identifying pre-2010 museum-series monographs with no DOI that Crossref doesn't index, including ones the bib dates wrongly.
- **archive.org:** single leaves via IIIF (see the archive.org entry below).
- **Google Books:** `books.google.com/books?id={VOL}&newbks=0&jscmd=SearchWithinVolume&q={term}` returns page numbers and snippets as JSON, even for no-preview volumes. A snippet shows that a word is in print on a page; it is not a reading of the passage, and a miss is not proof of absence (Google stems search terms).
- **The Wayback Machine:** a site that now refuses requests, or has gone, may still have its files captured. `web.archive.org/cdx/search/cdx?url={host}/{path}/*` lists the captured file names, and the `id_` form of a capture URL returns the original file rather than the Wayback page around it.

Crossref silence is not absence: museum series, state surveys and pre-1990 society journals often have no DOI.

### archive.org route for pre-1920 journals (single leaves via IIIF)
1. `archive.org/advancedsearch.php` with `title:(...) AND volume:N`.
2. `{id}_scandata.xml` gives the leafNum -> printed pageNumber map; don't guess the offset.
3. Fetch single leaves: `https://iiif.archive.org/iiif/{id}%24{leaf}/full/full/0/default.jpg` (`%24` is `$`), no need for the full jp2 zip or PDF.
4. Check every folio by eye, and check that the leaf after the last one is the next article.

Save the leaves as `source_images/{key}/`, then `corpus pdf stitch --key {key}` and vision OCR.

### Free public archives worth checking
- **SciEngine** (old Scientia Sinica and 科学通报 issues, free): look up the internal id from a DOI with `https://www.sciengine.com/sci-open/api/v1/open/article/basic?doi=<DOI>`, then `https://www.sciengine.com/doi/pdf/<id>`.
- **vertpala.ac.cn**: Vertebrata PalAsiatica back issues from 1989 are free; the PDF link carries a token, so fetch through the site's `showArticleFile.do` request. Pre-1989 papers are not online.
- **Smithsonian Polyglot Paleontologist translations**: the listing pages are behind Cloudflare, but the files download directly from `naturalhistory.si.edu/sites/default/files/media/translated_publications/`; find file names through the Wayback index. The same files sit under `paleoglot.org/files/` with a separate Wayback index that lists files the Smithsonian path lacks; the live paleoglot paths return 403, so download the Wayback `id_` copy. File names are loose (author and two-digit year), so check that each file is the paper you want. When the original isn't online, a translation can stand in under the paper's key; say so in the acquisition note.
- **Government and institutional archives**: NRCan OSTR/GEOSCAN (GSC Papers, National Museum of Canada Bulletins), Geoscience Australia eCat (BMR reports), BRGM InfoTerre (`ficheinfoterre.brgm.fr/Notices/{sheet}N.pdf` for French 1:50 000 map notices), USGS Pubs Warehouse, Missouri Digital Library, SEGEMAR repository, Fundación Miguel Lillo (Acta Geológica Lilloana), Revista Brasileira de Geociências (via its DOIs).
- **paleoarchive.com/literature/**: about 1,300 PDFs of Scandinavian geology, 1821–1998. curl needs a browser user agent. File names follow `Author[&Author|etal]YYYY-Title.pdf`.
- **Free, but refused to scripts**: teses.usp.br (USP theses), Persée PDFs, the LNEG geoportal, the Open University ORO, the Texas Tech repository, Gallica, Queensland GSQ open data, SARIG, Math-Net.ru. Download these in a browser; don't try to get a script past the block.
- **HathiTrust**: page images refuse scripted requests (a Cloudflare challenge), even with a browser user agent; do not try to get round it. The catalog API (`catalog.hathitrust.org/api/volumes/brief/htid/{id}.json`) still answers and gives title, rights code and other copies of the volume. Read full-view volumes in a browser.
- **CNKI** covers Chinese journals and theses, not books or symposium volumes, and doesn't answer scripted requests; search it in a browser. Its scans of old journals carry OCR layers that misread, so use vision; born-digital theses have a usable CJK layer for `scripts/correct_cjk_from_textlayer.py`.
- **Not available**: Paleontological Journal (English) on SpringerLink starts in 2006, so the 1966–1996 papers are not online there. PalAss Field Guides to Fossils are print-only.

### Conference abstracts and proceedings: where they end up
- Jornadas Argentinas de Paleontología de Vertebrados: abstracts are printed in Ameghiniana supplements.
- Congresso Brasileiro de Geologia: the SBG archive (`sbgeo.org.br/home/pages/44`, links under `anais_digitalizados/`) has most years from 1970 as free PDFs and zips, some very large (1980: 536 MB; download with `curl --http1.1 -C - --retry 5`, since a plain attempt can stall). 1980 (XXXI, Camboriú) holds only vols 3-4 (pp. ~1330-2510); vols 1-2 are not online.
- 7th European Workshop on Vertebrate Palaeontology (Sibiu 2002): doc.rero.ch is gone; the Wayback copy of record 16291 (PAL_E567.pdf) has only the abstracts half (pp. 1-40). The field guide is not online.
- National Science Museum (Tokyo) Monographs are online only from no. 23; no. 15 (Second Gondwanan Dinosaur Symposium) is not.
- USP's repository has single papers from the VII SSAGI (Brasília 2010), not the volume.
- Not found online at all: Salas de los Infantes Jornadas actas (1999, 2003), Isona egg symposium (2000), Geoscience Africa 2004, Mesa Southwest Museum Bulletin 7 (2000), V Simpósio sobre o Cretáceo do Brasil (1999), IX Congreso Geológico Argentino Relatorio (1984), IV and IX Congresos Geológicos Chilenos (1985, 2000), the 1970s-90s Congreso Geológico Argentino actas.

## BHL

### The BHL tier: match, review, fetch page images, vision OCR
`corpus bhl setup` and `corpus bhl index` build `.runtime/bhl_dumps/bhl.db` (about 0.55 GB: part, partpage, item, title, doi, partcreator, partidentifier; no per-page table). `corpus bhl match` matches keys without a PDF to a BHL Part, or else to the unique Item (volume), and prints a review table. DOI matches are accepted; fuzzy matches need the review, because they can land on a different paper by the same author or a paper from a different year. `corpus bhl fetch --key K` accepts K's match, downloads the JP2 page images from BHL's S3 bucket, stitches `pdfs/{key}.pdf` and flags it for vision OCR, so it appears in `/intake`. BHL's own OCR is not used as the markdown: it is too rough.

A page's sequence number in its volume (which names the JP2) comes from the bucket's OCR file names or, for the many volumes with no OCR in the bucket, from the API's `GetItemMetadata` page list, so set `BHL_API_KEY`. For volume matches the fetch keeps the pages whose printed numbers (from the API) fall in the cited range. When the cited pages aren't among BHL's printed numbers, or the citation gives none, pass the scans by hand: `corpus bhl fetch --key K --item-seqs FIRST-LAST`. Some items have malformed placeholder barcodes and no JP2s at all; list the S3 prefix (`https://bhl-open-data.s3.amazonaws.com/?list-type=2&prefix=images/{BarCode}/`) before assuming a fetch failure is transient.

### A BHL barcode is usually an archive.org identifier with full text
Many BHL items have no `ocr/item-{ID}/` on S3, but a BHL `BarCode` such as `bibliographycata00hayoiala` is usually the Internet Archive identifier. `curl -sL https://archive.org/download/{BarCode}/{BarCode}_djvu.txt` returns the whole volume's OCR text, enough to find a section and its printed page in a large catalogue or index volume. Then map the printed page to a scan with the API's page list.

### BHL page numbers are unreliable; pick article scans from a contact sheet
BHL's printed page numbers are assigned by hand and can be wrong. Plates and their blank versos are sometimes given the numbers of the text pages around them, so a page range can select half the plates and run on into the next article. Multi-section volumes repeat page numbers too: the Proceedings and Transactions of the Royal Society of Canada number each section from 1, so one volume can hold five scans labelled p. 51. When a cited page maps to more than one scan, or the article has plates, fetch the JP2s around it, tile them on a labelled contact sheet, read the folios and section headings, and choose the article's scans by eye (skip blank versos). Fetch the chosen range with `corpus bhl fetch --key K --item-seqs FIRST-LAST`, or put the JP2s in `bhl_images/{key}/page-{seq}.jp2` and run `corpus bhl stitch --key K --force`.

### BHL partpdf can deliver the wrong pages; check printed folios
A `biodiversitylibrary.org/partpdf/{PartID}` download (in a browser; scripts get the Cloudflare block) can contain pages other than the ones it claims while its own attribution page cites the right range. Every QC check then passes, because the OCR is faithful to the wrong pages. On any partpdf intake, read the printed folio on the first and last content pages. On a mismatch, rebuild from S3: the `part` table in `bhl.db` gives `ItemID` and `StartPageID`, `item` gives `BarCode`; pull `{BarCode}_{seq:04d}.jp2` for a window either side, crop the top ~700 px of each into one contact sheet, read the folios, then stitch the confirmed scans as above and run vision OCR.

### BHL parts can stop short of their own page range
A part record can give the full page range while its page list (and the partpdf built from it) ends partway, so the folios look continuous and the page count looks plausible. Compare the last content folio with the citation's end page, not only with the PDF's page count. The missing pages are usually the next scans of the same item: fetch them from S3, check the folios of the first and last scan and of the one after, and add them with `corpus pdf append-pages {key} --source {pdf} --pages {range}`. If the paper's existing pages went through marker, convert the appended image pages with vision and add that text to the markdown.

## Formats

### DjVu volumes: search the hidden OCR first, then extract only the needed pages
djvulibre (`brew install djvulibre`) provides `ddjvu`, `djvutxt`, `djvused` and `djvups`. `djvutxt --page=N` plus grep finds a taxon in seconds; `djvused -e 'n'` gives the page count. One page: `ddjvu -format=pdf -page=149 vol.djvu pdfs/{key}.pdf`. Whole volume: `ddjvu -format=tiff -mode=black` + `tiff2pdf` (about 22 MB, against 326 MB for `ddjvu -format=pdf` and 206 MB in 12 minutes via `djvups | gs`). No route keeps the hidden text layer, so the output needs vision (`corpus intake acquire {key} --vision-ocr REASON`), never marker. DjVu text layers on old scans misread taxon names, so use them only to find pages and to cross-check the vision text. Oversized image-only PDFs: OCR the full-resolution file, then `corpus pdf compress {key}` for the copy you keep. Books scanned as two-page spreads go through `corpus convert spread`.

### Book chapters from a book PDF you hold
For a key that is a chapter of a book you have as a PDF: find the page range (the PDF outline, or the Contents page plus one located chapter opener for the offset), cut it out with `corpus pdf extract-pages {key} {pages} --keep-source {path}` (on a copy placed at `pdfs/{key}.pdf`), and record where the book came from in the acquisition note. Identity traps:
- Currie & Padian's *Encyclopedia of Dinosaurs* (1997) is not Glut's *Dinosaurs: The Encyclopedia* (1997).
- *The Dinosauria* puts all references in back matter, so its chapters have no reference lists.
- A key can point at a whole edited volume when the citing record means one chapter; that needs a fix in the bib, not an extraction.
- HTML pages exported to PDF have no bookmarks and no constant page offset; map pages from the printed page markers in the text, and check the extract by its text, not only its page count.
- Old scanned books often have garbage OCR layers and need vision, not marker.

## Fetch tiers and identifiers

### The fetchers take --key/--keys-file; diff the manifest after a run
`corpus fetch unpaywall`, `corpus fetch oa-direct` and `corpus fetch pmc` take `--key K` (repeatable) and `--keys-file F` and reject unknown flags. With no keys they take every manifest key that has a DOI and no PDF (`fetch unpaywall` only those no tier has tried yet); with keys, only those. Many publisher landing pages refuse a non-browser user agent, so try `corpus fetch oa-direct --browser-ua` when it reports `landing_failed`. Diff `manifest.json` against HEAD after any fetcher run before committing.

### A run of identical failures is usually one canned page
When a tier reports a run of the same failure, compare response sizes per key. Byte-identical bodies across unrelated DOIs are one canned page (a challenge or a bot wall served as HTTP 200), not many genuine misses, and recording them as permanent "no match" hides papers that a browser download would get.

### Unpaywall can return an abstract page rendered as a PDF
`url_for_pdf` sometimes points to an abstract landing page rendered to PDF: valid `%PDF`, plausible size, correct title, authors and DOI on page 1. Tells: a TCPDF footer, a "Full text (NNN kB)" line, a page count far below the cited range. Acta Palaeontologica Polonica does this through a doubled `…/article/item/article/item/…?pdf=` URL; fetch APP papers from `app.pan.pl/archive/published/app{vol}/{articleid}.pdf` instead. After any DOI-based open-access fetch, compare the page count with the citation's range and look at anything under ~60%.

### Open-access PDFs behind a challenge page: the PMC Article Dataset
PeerJ (and europepmc.org's `?pdf=render`) answer scripted requests with a Cloudflare challenge, and PMC's own PDF links with a "Preparing to download" browser check; the old PMC OA web service (`oa.fcgi`) is gone. NCBI's public bucket works: `https://pmc-oa-opendata.s3.amazonaws.com/metadata/{PMCID}.{version}.json` gives `is_pmc_openaccess`, `license_code` and an MD5-checked `pdf_url`. `corpus fetch pmc` looks the PMCID up by DOI with Europe PMC's search API and downloads from there. Use it when Unpaywall lists open-access locations but no PDF link. PeerJ PDFs can be very large (tens of MB); compress before committing if they exceed your Git LFS limit.

### Supplements and licences for open-access articles: Europe PMC
peerj.com refuses scripted requests, so PeerJ supplements can't be fetched from the article page. Europe PMC serves the same files for any article in PMC (PeerJ, PLOS, Scientific Reports, eLife, BMC):

- Look up the PMCID, licence and whether supplements exist:
  `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:%22{doi}%22&format=json&resultType=core`
  (fields `pmcid`, `license` such as "cc by", `hasSuppl`).
- Download every supplement plus the figure JPGs as one zip:
  `https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/supplementaryFiles`.
- The licence URL with its version is in the article XML
  (`.../rest/{PMCID}/fullTextXML`, `<license xlink:href=...>`). This matters for
  PLOS, whose printed statement says "Creative Commons Attribution License"
  without a version.

The figure JPGs in the zip are about half the width of the images embedded in
the published PDF; for figures, extract from the PDF instead.

### Crossref matching: strip JATS tags and score title against title
Crossref titles carry `<i>...</i>` around taxon names; dropping punctuation without removing tags leaves stray `i` words and rejects correct top hits, so strip `<[^>]{1,40}>` first. Compare Crossref's title to the title parsed out of the citation, not to a citation prefix that starts with the authors. When parsing a title out of a citation, don't split on the first `". "` (initials, `gen. nov.`). A title-only retry is safe for entries `query.bibliographic` can't match, as long as verification is unchanged. Don't loosen the year gate to rescue a match: it is what stops a wrong DOI. An accepted match still needs a look; the usual wrong ones are a short generic title matching another paper, a book review of the cited book, and a whole-volume DOI for a chapter.

## Keeping sources

### Keep the full source of every extract, and record where it came from
Cutting a scan down in place loses the rest of it, and a source site can disappear or start refusing downloads later. Record the source URL or path in the acquisition note at intake. When extracting pages from a source that isn't committed anywhere, pass `--keep-source PATH` to `corpus pdf extract-pages` (and to `corpus pdf compress`) so the full file survives.

### Cite-scoped extracts from whole volumes
When a reference is a whole volume cited for one act (a name erected, a clade defined), an extract of the relevant section plus the title page and contents is usually enough and converts far faster than the full volume. Check the citing record's notes for what the citation is for, keep the full volume (above), and record the extracted range in the acquisition note.
