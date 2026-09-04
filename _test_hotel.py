import sys, fitz
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'd:\自己资料\自己资料\其他\报销小助手')
from services.invoice_parser import InvoiceParser

path = r'c:\Users\13529\Desktop\报销\6月报销\dzfp_26332000005820452221_杭州星灿生物技术有限公司_20260706160502.pdf'
doc = fitz.open(path)
text = ''
for page in doc:
    text += page.get_text()
doc.close()

print("=== 原始PDF文本 ===")
print(repr(text))
print()

p = InvoiceParser('', '')
result = p._try_parse_regex(text)
print(f"=== 正则解析结果 ===")
print(result)
