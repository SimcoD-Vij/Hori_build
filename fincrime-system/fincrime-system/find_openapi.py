import urllib.request
import re
try:
    html = urllib.request.urlopen('http://127.0.0.1:8001/docs').read().decode()
    urls = re.findall(r'url: "(.*openapi.json)"', html)
    print('OpenAPI URLs found:', urls)
except Exception as e:
    print('Error:', e)
