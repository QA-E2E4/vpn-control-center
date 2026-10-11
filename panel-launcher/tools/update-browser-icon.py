#!/usr/bin/env python3
from pathlib import Path
import datetime, os, re, shutil, sys, tempfile
TAG = '<link id="openflux-favicon" rel="icon" type="image/png" sizes="64x64" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAABmJLR0QA/wD/AP+gvaeTAAAFTElEQVR4nO2bXWwUVRTH/2e2pS2h3S2koICmmkCl1AApLfiIMbEfRg2GwIPRkBhfNJIQY1v1YRMIpX0oiVFffCLGmEg00aRfJMiT6baKhG6bgiEtsFBqpdKllLbbnXt8kG23+zV3du6M09rf0907Z86e+fd+nHt2Cqzy/4bM3tAx4S8iyqsHaS8CvAtAKQAfgFzVwWWEMcuEMAGDTAgwiZ/qvR/3mnUjLUD7g1PbPbrWwMARAGvNfpFDBJmppa945ls/+YXMDYYCfBdqK1i3bv4EgY8ByLEcojP8SgJHazY0DhoZZhTgfLh1W1TwDwSuUBebQzBmofFbtb6mc5nM0grQcb95D4G6AZQoD845BDEdq1nf8Hk6g5QCnA+3btOF+AXL++Hj4OO1xU1nUl1JEuDiiD9/1pcfALDL9ricQ0BDXa23sTvxgpbYMeMrOImV9fAAoEHg6+6Jk08lX4ij/cGp7Y9X+5VIifDktCZ2LhHAo2sNWD5bnXkYhzvDzVXxXQsCdEz4ix4nOSsZgk4n4jsWBCDKq4d7Mzx1EF5qH299IvZxcbj/m9sr/a7x22Ecf/2sUp+ZKNnixZkf3zYy83hyxBsAvgCWrAGsfOXvD9xU7TIjz+9PWuRTwhrVxdrxi+AzqgMKBkKqXWZkZ9XTUnbMi6l9vABFKoPRdYGh35wTgDRC+d4tcrbAplg7XoA1KgO6HhzDo4cRlS4zUlpWgkJfgax5XqyRlAmqYqD3ll2uU1KxT274J2KbAMEeZwXYWSW3ACZiiwCPpuYwPDRuh+uU5K7xoGz3k1nda4sAA30hCF2qIqWEsj2bkZuXXQZvS94flJz/h957Aa8drTI2tBFbRsCg5P6/o3KrHV9vCuUCjN2cxPho2NBuTX4Ont2xydDObpQL0N8rl/6W7d6MnFzbNiFplEcwIDn8n6uUy9rsRqkAui4wdOm2lK0b5j+gWIDr/XcxM22c/rpl/gOKBQgG5LY/t8x/QHEeILv/BwO38Obez0z7lyx4mELZn2H6wRxGhv5S5S4lsgUPMygTINh7y/b0V7bgYQZlAgxIzv9sMVPwMIM6AWw+/5sseEijRIDRG3/j3tiUCldpybbgYYQSAfodKH5kW/AwQokAdg9/KwUPIywLEJ0XuHrpjopY0mKl4GGEZa9/XBnF7My8oR1phC+730FhsfqFzAqWR4Ds6a+0rMR1Dw8oECAoef7fuc+eRcwqlgSYmpzBjWty6W+FDVmcCiwJMNAbAgvjX5TtXMWtYlEAyeOvjau4VeIFmDN7s6wALhz+C88aL4Cps+zt4QlM/PlQytaFC+BC2XpRAKIBMx5kf/sv9OajtMxl71sShmPNxXeEBNrN+JA9/pZXbQVppt/KtxeBK7Hm4gjQo98DkKpoROcFrv4ul/5WVLtu/gMaX1hoxho1JZ/cZeBC6juWcu3yHczNGqe/AFDhvvk/nR+Z64x9WDI2u8It1Sw4kNi/kiDGVzXrG9+NfV6SB9R4G/rAyPh+/TInEtX4dHxHUiKkcfRDAPccC8lJCG2v+JqG47uSBHh5w6chJj4MIOpYYE5A1MPetf7E7pSpcJ2v6WcADXbH5BwUEhE6WEcfJGW7ac8CtcWNbcR4H5Jbo3uhkGCur9/40VjKq0a3d042HwLTWQDuq2YYQdQjInQw3cMDEqfBWl/TOV2nSmKY/qfE/5AIiJvZW3Ag08MDJvZ7Zqau+6ePgKgB7v2XmmkwfaNroiVxtU9HVglPV7ilGixeZab9AMrBKAYhPxtfFogAmCTGiNDosga+mBeZ7Tiw0S93RF1lFQDAP1LokPXuo47JAAAAAElFTkSuQmCC">'
p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/opt/openflux-panel/static/index.html')
s = p.read_text(encoding='utf-8')
if '__PANEL_TOKEN__' not in s or '</head>' not in s or 'OpenFlux' not in s:
    raise SystemExit('Unexpected panel HTML; no changes made.')
pattern = r'<link\b[^>]*\bid="openflux-favicon"[^>]*>'
new = re.sub(pattern, TAG, s) if re.search(pattern, s) else s.replace('</head>', '  ' + TAG + '\n</head>', 1)
if new == s:
    print('Browser icon is already installed. Refresh the browser tab.'); sys.exit(0)
backup = p.with_name(p.name + '.before-icons-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
shutil.copy2(p, backup)
fd, temp = tempfile.mkstemp(dir=p.parent, prefix='.favicon-')
try:
    os.fchmod(fd, p.stat().st_mode & 0o777)
    with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(new)
    os.replace(temp, p)
finally:
    if os.path.exists(temp): os.unlink(temp)
print('Browser icon installed. Refresh the panel with Ctrl+F5.')
print('Backup:', backup)
