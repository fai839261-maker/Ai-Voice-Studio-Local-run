import re

def today_errors(text,day):
    entries=re.split(r'(?m)(?=^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\])',text)
    return ''.join(x for x in entries if x.startswith('['+day+' '))
