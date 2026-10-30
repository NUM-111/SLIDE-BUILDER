# Local-First AI Presentation Builder

A personal, loopback-only document-to-editable-PowerPoint application. There is no login, hosted service, cloud database, or external AI connection. This first milestone uses the Python standard library so it starts without installing packages.

## Start

Python 3.10 or later is required. From this folder, run:

```powershell
uv run --offline --python 3.14 python run.py
```

Or use an installed Python interpreter:

```powershell
python run.py
```

Open http://127.0.0.1:8765. If that port is in use, pass another local port, for example `python run.py --port 8766`. Stop the server with Ctrl+C. The server binds to `127.0.0.1` only.

## First milestone

- Imports PDF, DOCX, PPTX, and TXT documents and stores an unchanged copy in `data/uploads/`.
- Shows an inventory of extracted headings, paragraphs, bullets, and table rows with source locations and stable IDs.
- Reads text from text-based PDFs and OOXML documents without sending them elsewhere. Scanned PDFs require OCR and currently produce an extraction warning if no text is found.
- Accepts PNG and JPG logos. Common 8-bit non-interlaced RGB/RGBA PNG files get local color-candidate analysis. Candidates are suggestions and are not applied unless selected.
- Uses the attached FLOLIZ reference presentation's white, deep-indigo, red, and cyan palette for generated slides; company colors remain editable in the saved profile.
- Adds an optional framed picture area to each slide. Select a slide number, upload a PNG/JPG, preview its assignment, and remove or replace it before export. Pictures stay in local project storage and are embedded only on the chosen slide.
- Saves reusable brand profiles and local project state in JSON under `data/`.
- Generates editable PowerPoint text shapes, splitting long blocks and packing slides by text volume. Logos are embedded when uploaded.
- Checks generated marked content shapes against the canonical inventory and reports missing, duplicate, altered, and uncertain blocks before download.

## Source and brand notes

The attached AML PowerPoint identifies **FLOLIZ NIGERIA LTD** and supplies the reference theme colors: indigo `#1A0F49`, red `#C00000`, and cyan `#25C6E3`, alongside secondary accents. These colors are used as editable template defaults. Logo colors can also be inspected from a separately uploaded PNG.

## Limitations

- PDF text extraction is a lightweight local parser, not a full PDF layout engine; complex font encodings, columns, unusual compression, and reading order may be imperfect. Verify the inventory against the source before export. PDF tables are not reconstructed as cell grids.
- DOCX/PPTX extraction covers body paragraphs and table rows, not embedded charts, SmartArt, notes, headers, footers, or complex floating text.
- The exported deck is editable and content-preserving but uses a conservative text-first layout. It does not yet infer diagrams, create illustrations, render-check overflow, export PDF, or analyze sample decks/brand guides. Shape auto-fit is enabled, but visual overflow checking is not implemented.
- OCR, optional Ollama, SQLite, PDF rendering, and external services are not implemented. No document text is summarized, paraphrased, or removed during generation.
- The comparison verifies generated content markers/text, not the appearance after opening in PowerPoint. Always visually review the downloaded deck.
- The app has no authentication; it is intended for a single user on a trusted local computer. Do not expose the port to a network.

## Test

```powershell
uv run --offline --python 3.14 python -m unittest discover -s tests -v
```