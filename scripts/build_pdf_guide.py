"""Build the public manual. Optional dependency: pip install reportlab."""
from pathlib import Path
import re
from html import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether, Preformatted
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/pdf/Naukri_Automation_Complete_Guide.pdf'
BASE = 'https://github.com/alamuruharsha24/naukri-automation'
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='BodyGuide', fontName='Helvetica', fontSize=10, leading=14, spaceAfter=7, textColor=colors.HexColor('#25334a')))
styles.add(ParagraphStyle(name='Chapter', fontName='Helvetica-Bold', fontSize=24, leading=30, spaceAfter=20, textColor=colors.HexColor('#122944')))
styles.add(ParagraphStyle(name='Section', fontName='Helvetica-Bold', fontSize=14, leading=19, spaceBefore=12, spaceAfter=7, keepWithNext=True, textColor=colors.HexColor('#126b78')))
styles.add(ParagraphStyle(name='Subsection', parent=styles['Section'], fontSize=11, leading=15))
styles.add(ParagraphStyle(name='Cell', parent=styles['BodyGuide'], fontSize=8.7, leading=12, spaceAfter=0))
styles.add(ParagraphStyle(name='CodeGuide', fontName='Courier', fontSize=8, leading=12, backColor=colors.HexColor('#edf3f7'), borderPadding=10, spaceBefore=7, spaceAfter=13))
styles.add(ParagraphStyle(name='Prompt', parent=styles['BodyGuide'], backColor=colors.HexColor('#edf3f7'), borderPadding=12, spaceBefore=8, spaceAfter=16))
styles.add(ParagraphStyle(name='Cover', fontName='Helvetica-Bold', fontSize=38, leading=45, textColor=colors.HexColor('#122944'), spaceAfter=24))


def plain(s):
    for a,b in [('—','-'),('–','-'),('‑','-'),('’',"'"),('“','"'),('”','"'),('→','>')]:
        s=s.replace(a,b)
    return s


def inline(s):
    s=escape(plain(s))
    def link(m):
        label,url=m.groups()
        if not url.startswith(('https://','http://')):
            url=BASE+'/blob/main/docs/'+url
        return f'<link href="{url}" color="#126b78">{label}</link>'
    s=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',link,s)
    s=re.sub(r'`([^`]+)`',r'<font name="Courier">\1</font>',s)
    s=re.sub(r'\*\*([^*]+)\*\*',r'<b>\1</b>',s)
    return s


class Guide(SimpleDocTemplate):
    def afterFlowable(self, flow):
        if self.page >= 3 and isinstance(flow,Paragraph) and flow.style.name in ('Chapter','Section'):
            level=0 if flow.style.name=='Chapter' else 1
            text=flow.getPlainText()
            key='heading-'+str(self.seq.nextf('heading'))
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text,key,level=level,closed=False)
            self.notify('TOCEntry',(level,text,self.page,key))


def footer(canvas,doc):
    w,h=A4
    canvas.setStrokeColor(colors.HexColor('#d8e1e8'))
    canvas.line(44,42,w-44,42)
    canvas.setFillColor(colors.HexColor('#617184'))
    canvas.setFont('Helvetica',8)
    canvas.drawString(44,28,'NAUKRI AUTOMATION  /  COMPLETE GUIDE')
    canvas.drawRightString(w-44,28,str(doc.page))


def markdown(text):
    rows=plain(text).splitlines(); out=[]; i=0
    while i<len(rows):
        line=rows[i]
        if not line.strip(): i+=1; continue
        if line.startswith('# '): i+=1; continue
        if line.startswith('```'):
            lang=line[3:]; code=[]; i+=1
            while i<len(rows) and not rows[i].startswith('```'):
                code.append(rows[i]); i+=1
            i+=1
            if lang=='text':
                out.append(Paragraph(inline(' '.join(code)),styles['Prompt']))
            else:
                # Commands remain literal and are sized to fit without line wrapping.
                out.append(Preformatted('\n'.join(code),styles['CodeGuide']))
            continue
        if line.startswith('|'):
            data=[]
            while i<len(rows) and rows[i].startswith('|'):
                cells=[v.strip() for v in rows[i].strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+',v) for v in cells):
                    data.append([Paragraph(inline(v),styles['Cell']) for v in cells])
                i+=1
            table=Table(data,colWidths=[190,317],repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dcebf0')),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f5f8fa')]),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,0),0.6,colors.HexColor('#93b7bf'))]))
            out.extend([table,Spacer(1,10)]); continue
        if line.startswith('### '):
            out.append(Paragraph(inline(line[4:]),styles['Subsection'])); i+=1; continue
        if line.startswith('## '):
            out.append(Paragraph(inline(line[3:]),styles['Section'])); i+=1; continue
        if re.match(r'^(\d+\. |[-] )',line):
            out.append(Paragraph(inline(line),styles['BodyGuide'])); i+=1; continue
        para=[line]; i+=1
        while i<len(rows) and rows[i].strip() and not rows[i].startswith(('#','```','|','- ')) and not re.match(r'^\d+\. ',rows[i]):
            para.append(rows[i]); i+=1
        out.append(Paragraph(inline(' '.join(para)),styles['BodyGuide']))
    return out


def build():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    story=[Spacer(1,90),Paragraph('Naukri Automation',styles['Cover']),Paragraph('The complete user guide',styles['Chapter']),Paragraph('Install. Configure. Preview. Apply with intent.',styles['Section']),Paragraph('A practical manual for Windows, macOS, and Linux, with ready-to-copy prompts for Codex, Claude, Grok, Antigravity, and other AI assistants.',styles['BodyGuide']),Spacer(1,25),Paragraph('Includes every section of the user guide and all 10 AI prompt templates, plus the validation report and project links.',styles['BodyGuide']),Paragraph('Edition: 21 September 2026',styles['BodyGuide']),Paragraph(f'<link href="{BASE}" color="#126b78">{BASE}</link>',styles['BodyGuide']),PageBreak(),Paragraph('Contents',styles['Chapter'])]
    toc=TableOfContents(); toc.levelStyles=[ParagraphStyle(name='toc0',fontName='Helvetica-Bold',fontSize=10,leading=14,spaceBefore=7),ParagraphStyle(name='toc1',fontName='Helvetica',fontSize=8.5,leading=11,leftIndent=12)]
    story.extend([toc,PageBreak(),Paragraph('1. Start here',styles['Chapter'])])
    story.extend(markdown('''## Get the project
Open the repository link on the cover. Choose Code > Download ZIP and extract it. Or use Git:
```bash
git clone https://github.com/alamuruharsha24/naukri-automation.git
cd naukri-automation
```
## Requirements and workflow
You need Python 3.10+, a supported desktop operating system, internet access, your own Naukri account, and a local resume. AI provider keys are optional. Setup creates local configuration; login captures your own session; doctor checks the installation; preview reads results; apply --confirm submits real applications.
Review the platform's current terms before running automation. Challenges and account restrictions stop the workflow. Keep passwords, session cookies, resumes, and API keys private.
## Important command difference
Use the new CLI commands in this guide. The legacy command python naukri_apply.py starts real applications immediately and must not be used as a test.
## How to use this manual
Follow chapter 2 in order for your first setup. Chapter 3 contains prompts to give an AI assistant. Chapter 4 records the original validation and its limits. PDF links are clickable, and commands and prompts are selectable text.
'''))
    for title,file in [('2. Complete user guide','USER_GUIDE.md'),('3. AI assistant prompts','AI_PROMPTS.md'),('4. Validation and limits','VALIDATION.md')]:
        story.extend([PageBreak(),Paragraph(title,styles['Chapter'])]); story.extend(markdown((ROOT/'docs'/file).read_text()))
    story.extend([PageBreak(),Paragraph('5. Project links and attribution',styles['Chapter'])])
    for label,url in [('GitHub repository',BASE),('Latest README',BASE+'#readme'),('User guide source',BASE+'/blob/main/docs/USER_GUIDE.md'),('Prompt guide source',BASE+'/blob/main/docs/AI_PROMPTS.md'),('Automated check results',BASE+'/actions'),('Original project','https://github.com/Hemanth-kumar-N-arya/naukri-job-apply-ai')]:
        story.append(Paragraph(f'<b>{label}</b><br/><link href="{url}" color="#126b78">{url}</link>',styles['BodyGuide']))
    story.append(Paragraph('Distributed under the MIT license. Original license is preserved in the repository. This manual describes the shareable edition; live site behavior and third-party services can change.',styles['BodyGuide']))
    doc=Guide(str(OUT),pagesize=A4,rightMargin=44,leftMargin=44,topMargin=45,bottomMargin=58,title='Naukri Automation - Complete User Guide',author='Naukri Automation',pageCompression=1)
    doc.multiBuild(story,onFirstPage=footer,onLaterPages=footer)
    print(OUT)


if __name__=='__main__': build()
