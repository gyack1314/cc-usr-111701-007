"""标准库 HTTP 传输;不自动跟随重定向,不自动附加任何授权头。"""
import http.client
from urllib.parse import urlsplit

class HttpTransport:
    """每次请求独立连接,响应体完整读入;分块传输编码由 http.client 处理。"""
    def __init__(self,timeout=30):self.timeout=timeout
    def request(self,method,url,headers=None):
        u=urlsplit(url)
        if u.scheme not in ('http','https') or not u.hostname:raise ValueError('地址非法')
        cls=http.client.HTTPSConnection if u.scheme=='https' else http.client.HTTPConnection
        conn=cls(u.hostname,u.port,timeout=self.timeout)
        target=u.path or '/'
        if u.query:target+='?'+u.query
        try:
            conn.request(method.upper(),target,headers=dict(headers or {}))
            resp=conn.getresponse()
            return resp.status,{k.lower():v for k,v in resp.getheaders()},resp.read()
        except http.client.HTTPException as e:
            raise ConnectionError(str(e)) from e
        finally:
            conn.close()
