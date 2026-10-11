"""Bounded, anonymous, read-only editor preflight for new Yandex profiles.

No cookies persist, no challenge is solved, no WebSocket is opened. Successful
metadata validation is NOT proof of working VPN traffic or LTE reachability.
"""
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

LIMIT=2*1024*1024
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:153.0) Gecko/20100101 Firefox/153.0'
URL_RE=re.compile(r'https://(?:(?:docs|disk)\.yandex\.ru/edit/d/[A-Za-z0-9_-]+(?:\?from_public=1)?|disk\.yandex\.ru/i/[A-Za-z0-9_-]+)')

MESSAGES={
 'viewer':'Яндекс вернул страницу просмотра файла, а не совместный редактор. Откройте редактирование по ссылке и укажите адрес редактора /edit/d/… . Подключение не создано.',
 'login':'Редактор требует вход в Яндекс с VPS. Нужен документ с публичным доступом на редактирование. Подключение не создано.',
 'captcha':'Яндекс требует проверку с адреса VPS; пригодность редактора пока не подтверждена. Подключение не создано. При белых списках Direct не сможет передать проверку узла.',
 'denied':'Яндекс отказал VPS в доступе к редактору (HTTP 403). Проверьте доступ на редактирование по ссылке. Подключение не создано.',
 'readonly':'Документ открыт только для чтения. Разрешите редактирование по ссылке. Подключение не создано.',
 'unsupported':'Страница не содержит конфигурацию, пригодную для транспорта Yandex Docs. Нужен совместный редактор OnlyOffice, а не просмотр файла или Volga. Подключение не создано.',
 'unavailable':'Не удалось безопасно проверить редактор с VPS. Повторите позже; подключение не создано.',
}

class DocumentCheckError(ValueError):
    def __init__(self, code):
        self.code=code
        super().__init__(MESSAGES[code])

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def classify(body):
    # Match the installed Go parser's single-line client-config contract.
    m=re.search(r'<script[^>]*id="client-config"[^>]*>(.*?)</script>',body)
    if not m:
        if re.search(r'id=["\'](?:store-prefetch|fedya-config)["\']',body):raise DocumentCheckError('viewer')
        raise DocumentCheckError('unsupported')
    try:
        cfg=json.loads(m[1]);office=cfg.get('officeActionData') or {}
        editor=office.get('editor_config') or {};doc=editor.get('document') or {}
        if not all(isinstance(v,dict) for v in (cfg,office,editor,doc)):raise ValueError()
        if (doc.get('permissions') or {}).get('edit') is False:raise DocumentCheckError('readonly')
        if not all(isinstance(v,str) and v for v in (office.get('balancer_url'),editor.get('token'),doc.get('key'))):raise ValueError()
    except DocumentCheckError:raise
    except (ValueError,TypeError,AttributeError):raise DocumentCheckError('unsupported') from None
    return {'status':'editor_metadata_ready','vpn_verified':False}

def check_yandex_document(url):
    if not isinstance(url,str) or not URL_RE.fullmatch(url):raise DocumentCheckError('unavailable')
    opener=build_opener(NoRedirect());current=url;deadline=time.monotonic()+18
    try:
        for _ in range(6):
            if time.monotonic()>=deadline:raise DocumentCheckError('unavailable')
            parsed=urlsplit(current)
            if parsed.scheme!='https' or parsed.hostname not in ('disk.yandex.ru','docs.yandex.ru') or parsed.username or parsed.password or parsed.port not in (None,443):raise DocumentCheckError('unavailable')
            try:response=opener.open(Request(current,headers={'User-Agent':UA}),timeout=min(6,deadline-time.monotonic()))
            except HTTPError as error:response=error
            with response:
                if 300<=response.status<400:
                    location=response.headers.get('Location')
                    if not location:raise DocumentCheckError('unavailable')
                    target=urljoin(current,location);p=urlsplit(target)
                    if p.hostname in ('disk.yandex.ru','docs.yandex.ru') and 'showcaptcha' in p.path:raise DocumentCheckError('captcha')
                    if p.hostname and (p.hostname=='passport.yandex.ru' or p.hostname=='passport.yandex.com'):raise DocumentCheckError('login')
                    current=target;continue
                if response.status==403:raise DocumentCheckError('denied')
                if response.status!=200:raise DocumentCheckError('unavailable')
                body=bytearray()
                while len(body)<=LIMIT:
                    if time.monotonic()>=deadline:raise DocumentCheckError('unavailable')
                    part=response.read(min(65536,LIMIT+1-len(body)))
                    if not part:break
                    body.extend(part)
                if len(body)>LIMIT:raise DocumentCheckError('unavailable')
                return classify(body.decode('utf8','replace'))
    except DocumentCheckError:raise
    except (OSError,URLError,ValueError):raise DocumentCheckError('unavailable') from None
    raise DocumentCheckError('unavailable')
