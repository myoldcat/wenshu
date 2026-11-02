from pathlib import Path
import json
from docx import Document
from docx.shared import Mm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.architecture-work'
WORK.mkdir(exist_ok=True)

def setfont(obj,size=10,bold=False,color='000000',family='Microsoft YaHei'):
    obj.font.name=family; obj.font.size=Pt(size); obj.font.bold=bold
    obj.font.color.rgb=RGBColor.from_string(color)
    pr=obj.element.get_or_add_rPr()
    rf=pr.find(qn('w:rFonts'))
    if rf is None: rf=OxmlElement('w:rFonts'); pr.insert(0,rf)
    for k in ['ascii','hAnsi','eastAsia','cs']: rf.set(qn('w:'+k),family)
    for k in ['asciiTheme','hAnsiTheme','eastAsiaTheme','cstheme']: rf.attrib.pop(qn('w:'+k),None)

class DesignDoc:
    def __init__(self):
        self.doc=Document(); self.pages=[]; self.text=[]; self.tables=[]; self.sources=[]
        s=self.doc.sections[0]; s.page_width=Mm(210);s.page_height=Mm(297)
        s.top_margin=Mm(18);s.bottom_margin=Mm(18);s.left_margin=Mm(20);s.right_margin=Mm(20);s.footer_distance=Mm(8)
        for name,sz,bold in [('Normal',10,False),('Title',23,True),('Subtitle',12,False),('Heading 1',17,True),('Heading 2',12,True),('Heading 3',10,True)]:
            st=self.doc.styles[name];setfont(st,sz,bold)
            st.font.italic=False
            pf=st.paragraph_format;pf.line_spacing=1.16;pf.space_after=Pt(5);pf.space_before=Pt(0 if name=='Normal' else 9)
            pf.widow_control=True
            if name.startswith('Heading'):pf.keep_with_next=True
            pr=st.element.find(qn('w:pPr'))
            if pr is not None:
                for el in list(pr):
                    if el.tag in (qn('w:pBdr'),qn('w:shd')):pr.remove(el)
        fp=s.footer.paragraphs[0];fp.alignment=WD_ALIGN_PARAGRAPH.RIGHT
        setfont(fp.add_run('KooHub  技术选型与系统详细设计  |  '),8,color='666666')
        fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');fp._p.append(fld)
        cp=self.doc.core_properties;cp.title='KooHub 技术选型与系统详细设计';cp.subject='单人本地知识库与写作软件';cp.author='KooHub';cp.comments=''
    def title(self,t):self.doc.add_paragraph(t,style='Title');self.text.append(t)
    def subtitle(self,t):self.doc.add_paragraph(t,style='Subtitle');self.text.append(t)
    def page(self,t):
        if self.pages:self.doc.add_page_break()
        self.pages.append(t);self.h(t,1)
    def h(self,t,l=2):self.doc.add_paragraph(t,style=f'Heading {l}');self.text.append(t)
    def p(self,t,boldlead=None):
        p=self.doc.add_paragraph()
        if boldlead and t.startswith(boldlead):p.add_run(boldlead).bold=True;p.add_run(t[len(boldlead):])
        else:p.add_run(t)
        self.text.append(t);return p
    def code(self,t):
        for line in t.splitlines():
            p=self.doc.add_paragraph();p.paragraph_format.space_after=Pt(0);p.paragraph_format.line_spacing=1.05
            p.paragraph_format.left_indent=Mm(3)
            setfont(p.add_run(line),8.5,family='Consolas')
        self.text.append(t)
        self.doc.add_paragraph().paragraph_format.space_after=Pt(1)
    def table(self,headers,rows,widths,cell_pad=85):
        t=self.doc.add_table(rows=1,cols=len(headers));t.autofit=False;t.alignment=WD_TABLE_ALIGNMENT.CENTER
        for col,w in zip(t.columns,widths):col.width=Mm(w)
        pr=t._tbl.tblPr;borders=OxmlElement('w:tblBorders')
        for side in ['top','left','bottom','right','insideH','insideV']:
            b=OxmlElement('w:'+side);b.set(qn('w:val'),'single');b.set(qn('w:sz'),'4');b.set(qn('w:color'),'D9D9D9');borders.append(b)
        pr.append(borders);m=OxmlElement('w:tblCellMar')
        for side in ['top','left','bottom','right']:
            z=OxmlElement('w:'+side);z.set(qn('w:w'),str(cell_pad) if side in ['top','bottom'] else '105');z.set(qn('w:type'),'dxa');m.append(z)
        pr.append(m)
        for j,x in enumerate(headers):t.rows[0].cells[j].text=x
        for row in rows:
            cells=t.add_row().cells
            for j,x in enumerate(row):cells[j].text=str(x)
        for i,row in enumerate(t.rows):
            rp=row._tr.get_or_add_trPr();rp.append(OxmlElement('w:cantSplit'))
            if i==0:rp.append(OxmlElement('w:tblHeader'))
            for j,c in enumerate(row.cells):
                c.width=Mm(widths[j]);c.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
                shd=OxmlElement('w:shd');shd.set(qn('w:fill'),'183349' if i==0 else ('F2F5F7' if i%2==0 else 'FFFFFF'));c._tc.get_or_add_tcPr().append(shd)
                for p in c.paragraphs:
                    pf=p.paragraph_format;pf.space_before=Pt(0);pf.space_after=Pt(0);pf.line_spacing=1.1
                    if widths[j]<=20:p.alignment=WD_ALIGN_PARAGRAPH.CENTER
                    for r in p.runs:setfont(r,8.8,i==0,'FFFFFF' if i==0 else '000000')
        gap=self.doc.add_paragraph();gap.paragraph_format.space_after=Pt(0);gap.paragraph_format.line_spacing=.4
        self.tables.append({'headers':headers,'rows':rows});self.text.extend(' | '.join(map(str,row)) for row in rows)
    def image(self,path,caption,width=170):
        self.doc.add_picture(str(path),width=Mm(width));p=self.p(caption);p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        for r in p.runs:setfont(r,8.5,color='555555')
        for el in self.doc.inline_shapes[-1]._inline.iter(qn('wp:docPr')):el.set('descr',caption)
    def source(self,sid,title,url,note):
        p=self.p(f'[{sid}] {title}。{note}')
        p.paragraph_format.keep_with_next=True
        for r in p.runs:setfont(r,9)
        p=self.doc.add_paragraph();p.paragraph_format.space_after=Pt(8)
        rel=p.part.relate_to(url,RT.HYPERLINK,is_external=True)
        link=OxmlElement('w:hyperlink');link.set(qn('r:id'),rel)
        rr=OxmlElement('w:r');rpr=OxmlElement('w:rPr');sz=OxmlElement('w:sz');sz.set(qn('w:val'),'17');rpr.append(sz)
        color=OxmlElement('w:color');color.set(qn('w:val'),'215B7D');rpr.append(color);rr.append(rpr)
        tt=OxmlElement('w:t');tt.text=url;rr.append(tt);link.append(rr);p._p.append(link)
        self.sources.append({'id':sid,'title':title,'url':url,'note':note})
    def save(self):
        target=ROOT/'docs-dev'/'KooHub_技术选型与系统详细设计_V1.0.docx'
        target.parent.mkdir(exist_ok=True)
        self.doc.save(target)
        manifest={'document':str(target),'date':'2026-10-21','pages_planned':len(self.pages),'sections':self.pages,'tables':self.tables,'sources':self.sources}
        (WORK/'design_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        (WORK/'design_text.txt').write_text('\n'.join(self.text),encoding='utf-8')
        print(json.dumps({'document':str(target),'planned_pages':len(self.pages),'tables':len(self.tables),'sources':len(self.sources)},ensure_ascii=True))

def architecture_image():
    im=Image.new('RGB',(1800,850),'white');d=ImageDraw.Draw(im)
    regular=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',30)
    small=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',25)
    bold=ImageFont.truetype('C:/Windows/Fonts/msyhbd.ttc',34)
    def textcenter(box,lines):
        x0,y0,x1,y1=box;y=(y0+y1)/2-len(lines)*24
        for idx,line in enumerate(lines):
            f=bold if idx==0 else small; bb=d.textbbox((0,0),line,font=f);d.text(((x0+x1-bb[2])/2,y),line,font=f,fill='#163348');y+=48
    def box(rect,lines,fill='#EEF4F7'):
        d.rounded_rectangle(rect,radius=14,fill=fill,outline='#7595A8',width=3);textcenter(rect,lines)
    def arrow(x0,y0,x1,y1):
        d.line((x0,y0,x1,y1),fill='#4D7189',width=5)
        import math
        a=math.atan2(y1-y0,x1-x0);s=16
        d.polygon([(x1,y1),(x1-s*math.cos(a-.5),y1-s*math.sin(a-.5)),(x1-s*math.cos(a+.5),y1-s*math.sin(a+.5))],fill='#4D7189')
    d.rounded_rectangle((15,65,1335,820),radius=24,outline='#183349',width=4)
    d.text((35,12),'本地运行边界',font=bold,fill='#183349')
    box((50,105,530,260),['浏览器写作工作台','React  Tiptap  图谱与素材'])
    box((610,105,1290,260),['Web 与 API','静态资源  REST  SSE  模式与证据校验'])
    box((50,365,530,535),['PostgreSQL 与 pgvector','业务版本  全文与向量  图谱  任务'])
    box((610,365,1290,535),['后台 Worker','采集  加工  检索  写作  导出'])
    box((50,635,530,775),['本地内容寻址文件库','原始快照  图片  模板  导出件'])
    box((610,635,1290,775),['模型与搜索适配层','能力探测  预算  限流  访问策略'])
    box((1390,240,1760,420),['外部模型接口','文本  视觉  Embedding'],'#F7F2EB')
    box((1390,535,1760,715),['互联网公开内容','可选检索与核验'],'#F7F2EB')
    arrow(530,180,610,180);arrow(940,260,940,365);arrow(610,430,530,430)
    arrow(705,260,380,365);arrow(290,535,290,635);arrow(775,535,500,635)
    arrow(950,535,950,635);arrow(1290,685,1390,625);arrow(1290,665,1390,370)
    path=WORK/'architecture.png';im.save(path);return path
