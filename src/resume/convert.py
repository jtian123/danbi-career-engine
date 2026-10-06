"""Word → PDF → page image on an ordinary Mac, no Codex runtime and no Word popup.

PDF: LibreOffice headless with an isolated profile (never Microsoft Word, whose sandbox
asks for file access). LibreOffice bundles Carlito, a metric twin of Calibri, so page
measurements match Word closely. Page image: pypdfium2 (a pip wheel, no Homebrew).
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

_CANDIDATES = ('/Applications/LibreOffice.app/Contents/MacOS/soffice', 'soffice', 'libreoffice')


def soffice():
    for p in _CANDIDATES:
        if p.startswith('/'):
            if Path(p).exists():
                return p
        elif shutil.which(p):
            return shutil.which(p)
    return None


def docx_to_pdf(docx: Path, outdir: Path) -> Path:
    binary = soffice()
    if not binary:
        raise RuntimeError('LibreOffice is not installed. Install it once: brew install --cask libreoffice '
                           '(or download from libreoffice.org), then rebuild.')
    outdir.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix='lo_profile_')   # isolated: no "already running" lock
    try:
        r = subprocess.run([binary, f'-env:UserInstallation=file://{profile}', '--headless', '--convert-to', 'pdf',
                            '--outdir', str(outdir), str(docx)], capture_output=True, text=True, timeout=180)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    pdf = outdir / (Path(docx).stem + '.pdf')
    if r.returncode or not pdf.is_file():
        raise RuntimeError('LibreOffice could not convert the résumé: ' + (r.stderr or r.stdout)[-400:])
    return pdf


def pdf_to_pngs(pdf: Path, outdir: Path, scale: float = 2.0) -> list:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    out = []
    try:
        for i in range(len(doc)):
            img = doc[i].render(scale=scale).to_pil()
            p = outdir / f'page-{i + 1}.png'
            img.save(p)
            out.append(p)
    finally:
        doc.close()
    return out


if __name__ == '__main__':   # python3 -m src.resume.convert draft.docx OUTDIR
    import sys
    d = Path(sys.argv[2])
    pdf = docx_to_pdf(Path(sys.argv[1]), d)
    pdf_to_pngs(pdf, d)
    print(pdf)
