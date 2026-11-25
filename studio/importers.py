"""Local text and Word extraction without executing document macros."""
from pathlib import Path
import zipfile
from xml.etree import ElementTree as ET

DOC_FILTER='Script files (*.txt *.md *.docx);;Word documents (*.docx);;Text files (*.txt *.md)'

def read_script(path):
    path=Path(path)
    if path.suffix.lower() in ('.txt','.md'):return path.read_text(encoding='utf-8-sig')
    if path.suffix.lower()=='.docx':
        with zipfile.ZipFile(path) as z:
            info=z.getinfo('word/document.xml')
            if info.file_size>20*1024*1024:raise ValueError('Word document XML exceeds 20 MB.')
            xml=ET.fromstring(z.read(info))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paras=[]
        for p in xml.findall('.//w:body//w:p',ns):
            t=''.join(x.text or '' for x in p.findall('.//w:t',ns))
            if t.strip():paras.append(t)
        return '\n\n'.join(paras)
    raise ValueError('Use TXT, MD or DOCX. Save legacy DOC documents as DOCX first.')

