"""Inspect rendered artifacts (pypdf + pdfplumber); no visual verdict inferred. Fill band is advisory."""
import json
import sys
import unicodedata
from pathlib import Path
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import pdfplumber
from pypdf import PdfReader


def norm(s):
    return ''.join(unicodedata.normalize('NFKC',s).split()).replace('–','-').replace('—','-')


def check(directory):
    directory=Path(directory);resume=json.loads((directory/'resume.json').read_text());cfg=json.loads((directory/'config.json').read_text())
    pdf=PdfReader(directory/'draft.pdf');text='\n'.join(p.extract_text() or '' for p in pdf.pages)
    issues=[]
    if len(pdf.pages)!=1:issues.append('PDF is not exactly one page')
    expected=[resume['name'],resume['headline'],resume['summary']]
    for e in resume['education']:
        expected.extend(e[k] for k in ('school', 'degree', 'dates'))
        expected.extend(e.get('coursework', []))
    for e in resume['experience']:expected.extend([e['company'],e['title'],e['dates'],*e['bullets']])
    for s in resume['skills']:expected.extend(s['items'])
    missing=[s for s in expected if s and norm(s) not in norm(text)]
    if missing:issues.append('Text missing or damaged in PDF extraction')
    with ZipFile(directory/'draft.docx') as z:
        xml=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        if xml.findall('.//w:tbl',ns):issues.append('Resume must remain a single-column paragraph document')
        if xml.findall('.//w:pBdr',ns) or ET.fromstring(z.read('word/styles.xml')).findall('.//w:pBdr',ns):
            issues.append('Unexpected paragraph or title border')
        sizes=[int(n.attrib['{'+ns['w']+'}val'])/2 for n in xml.findall('.//w:sz',ns)]
        if sizes and min(sizes)<9.5:issues.append('Type smaller than contact-line minimum')
        rels=ET.fromstring(z.read('word/_rels/document.xml.rels'))
        urls=[r.attrib['Target'] for r in rels if r.attrib.get('TargetMode')=='External']
    for e in resume['experience']:
        if e['company'] in resume['product_links'] and resume['product_links'][e['company']] not in urls:
            issues.append('Missing product hyperlink: '+e['company'])
    if resume['contact']['linkedin'] not in urls:issues.append('Missing LinkedIn hyperlink')
    geometry=[]
    with pdfplumber.open(directory/'draft.pdf') as pdf_geo:
        for p in pdf_geo.pages:
            words=p.extract_words()
            if words:
                box=[min(w['x0'] for w in words),min(w['top'] for w in words),max(w['x1'] for w in words),max(w['bottom'] for w in words)]
                geometry.append({'page':p.page_number,'bounds':box,'page_size':[p.width,p.height],'height_fraction':round((box[3]-box[1])/(p.height-2*cfg['margin']*72),3)})
                if box[0]<25 or box[2]>p.width-25 or box[1]<25 or box[3]>p.height-25:issues.append('Content is outside safe page margins')
    advisories=[]
    lo,hi=cfg.get('fill_band',[0.85,0.98])
    if geometry and len(pdf.pages)==1:
        f=geometry[0]['height_fraction']
        if f<lo:advisories.append(f'Page is only {f:.0%} full — target ~90–95%. Select one more strong claim or a fuller variant; never pad.')
        elif f>hi:advisories.append(f'Page is {f:.0%} full — it may look crowded; consider a concise variant.')
    return {'page_count':len(pdf.pages),'issues':issues,'advisories':advisories,'missing_text':missing,'geometry':geometry,'links':urls,
            'status':'pass' if not issues else 'revise','visual_review':'Required: inspect every page image; geometry is not a visual approval'}


if __name__=='__main__':
    result=check(sys.argv[1]);Path(sys.argv[1],'artifact_qa.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
