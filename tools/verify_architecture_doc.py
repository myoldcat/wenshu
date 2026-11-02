from pathlib import Path
import json,re,zipfile,hashlib
import pypdfium2 as pdfium
from pypdf import PdfReader
from docx import Document

root=Path(__file__).resolve().parents[1]
work=root/'.architecture-work'
manifest=json.loads((work/'design_manifest.json').read_text(encoding='utf-8'))
docpath=root/'docs-dev'/Path(manifest['document']).name
pdfpath=work/'render'/'architecture.pdf'
pdf=pdfium.PdfDocument(str(pdfpath)); reader=PdfReader(str(pdfpath)); pages=[]
for i,page in enumerate(pdf):
    img=page.render(scale=1.6).to_pil();img.save(work/'render'/f'page-{i+1}.png')
    text=reader.pages[i].extract_text() or ''
    (work/'render'/f'page-{i+1}.txt').write_text(text,encoding='utf-8')
    pages.append({'page':i+1,'characters':len(text),'first_text':text[:100],'last_text':text[-90:]})
doc=Document(docpath)
text='\n'.join(p.text for p in doc.paragraphs)+'\n'+'\n'.join(' | '.join(c.text for c in r.cells) for t in doc.tables for r in t.rows)
source_ids={x['id'] for x in manifest['sources']}
used=set(re.findall(r'\[(S\d+)\]',text));assert used <= source_ids
assert len(pdf)==manifest['pages_planned'], {'actual':len(pdf),'planned':manifest['pages_planned']}
assert all(p['characters']>150 for p in pages)
assert all(x in text for x in ['EvidencePack','ExportIR','unknown_outcome','source_revision','lease','SQLite','LanceDB','人工','章节'])
with zipfile.ZipFile(docpath) as z: assert z.testzip() is None
report={'document':str(docpath),'renderer':'Microsoft Word','pages':pages,'tables':len(doc.tables),'sources':len(source_ids),'planned_pages_match':True,'zip_valid':True,'sha256':hashlib.sha256(docpath.read_bytes()).hexdigest(),'visual_review':'pending','technical_review':'pending'}
(work/'qa_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pages':len(pdf),'tables':len(doc.tables),'sources':len(source_ids),'page_boundaries':[x['first_text'][:45] for x in pages]},ensure_ascii=False))
