from pathlib import Path
import json, re, zipfile
import pypdfium2 as pdfium
from pypdf import PdfReader
from docx import Document

root=Path(__file__).resolve().parents[1]
work=root/'.document-work'
pdfpath=work/'render'/'requirements.pdf'
manifest=json.loads((work/'requirements_manifest.json').read_text(encoding='utf-8'))
docpath=root/'docs-dev'/Path(manifest['document']).name
pdf=pdfium.PdfDocument(str(pdfpath))
reader=PdfReader(str(pdfpath))
pages=[]
for i,page in enumerate(pdf):
    image=page.render(scale=1.6).to_pil()
    image.save(work/'render'/f'page-{i+1}.png')
    text=reader.pages[i].extract_text() or ''
    (work/'render'/f'page-{i+1}.txt').write_text(text,encoding='utf-8')
    pages.append({'page':i+1,'characters':len(text),'first_text':text[:80],'last_text':text[-80:]})

doc=Document(docpath)
ids={item['id'] for item in manifest['requirements']}
refs=set()
for row in manifest['acceptance']:
    refs.update(row[1].split())
assert len(ids)==44
assert len(manifest['acceptance'])==20
assert refs==ids, {'uncovered':sorted(ids-refs),'unknown':sorted(refs-ids)}
assert len(pdf)==13
assert all(x['characters']>200 for x in pages)
with zipfile.ZipFile(docpath) as z:
    assert z.testzip() is None
    xml=z.read('word/document.xml').decode('utf-8')
    assert all(x in xml for x in ['章节模式','参考范文','输出格式模板','互联网核验','万级网页','数万字','人工编辑','向量索引'])
    assert 'TOC \\o' not in xml
    assert 'turn0search' not in xml
report={'document':str(docpath),'renderer':'Microsoft Word','pages':pages,'requirements':len(ids),'acceptance_cases':20,'all_requirements_covered':True,'tables':len(doc.tables),'zip_valid':True,'visual_review':'pending'}
(work/'qa_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pages':len(pdf),'requirements':len(ids),'acceptance_cases':20,'all_requirements_covered':True,'tables':len(doc.tables)},ensure_ascii=True))
