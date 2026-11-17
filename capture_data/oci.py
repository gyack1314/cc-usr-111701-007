"""OCI Distribution 不可变闭包获取。

先冻结 tag 实际解析到的 digest,再沿 index→平台 manifest→config/layer blob 收集字节。
所有对象以 digest 与长度核验后才入缓存;授权头只发往注册表本源,令牌端点与跨域
重定向不携带授权;分段响应按 Content-Range 组装;tag 在抓取中移动时保留旧目标与
新观测;中断后用同一状态续抓,只复用已核验对象,临时片段一律作废。
"""
import hashlib,json,re
from pathlib import Path
from urllib.parse import urlsplit,urlencode,urljoin
from .store import check

INDEX_TYPES={'application/vnd.oci.image.index.v1+json','application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST_TYPES={'application/vnd.oci.image.manifest.v1+json','application/vnd.docker.distribution.manifest.v2+json'}
ACCEPT=', '.join(sorted(INDEX_TYPES|MANIFEST_TYPES))
MAX_REDIRECTS=5
MAX_TOKEN_REFRESH=2
MAX_DEPTH=4
BLOB_RETRIES=3

class FetchError(Exception):
    """HTTP 状态或内容身份失败;归属当前平台,不中断整轮。"""

def origin(url):
    u=urlsplit(url)
    return (u.scheme,u.hostname,u.port or (443 if u.scheme=='https' else 80))

def classify(doc,content_type=None):
    ct=(content_type or '').split(';')[0].strip()
    mt=doc.get('mediaType') if isinstance(doc,dict) else None
    if ct in INDEX_TYPES or mt in INDEX_TYPES or (isinstance(doc,dict) and 'manifests' in doc):return 'index'
    if ct in MANIFEST_TYPES or mt in MANIFEST_TYPES or (isinstance(doc,dict) and 'config' in doc):return 'manifest'
    raise ValueError('媒体类型未知')

def mime_of(doc,content_type=None):
    if isinstance(doc,dict) and doc.get('mediaType'):return doc['mediaType']
    return (content_type or '').split(';')[0].strip() or None

def parse_challenge(value):
    m=re.match(r'\s*Bearer\s+(.*)',value or '',re.I)
    if not m:return None
    return dict(re.findall(r'(\w+)="([^"]*)"',m.group(1)))

def parse_content_range(value):
    m=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)',(value or '').strip())
    if not m:raise ValueError('分段头非法')
    return int(m.group(1)),int(m.group(2)),(None if m.group(3)=='*' else int(m.group(3)))

class RegistryClient:
    """按 digest 取件的注册表客户端。授权头只附加到注册表本源。"""
    def __init__(self,transport,registry,repository):
        self.transport=transport if callable(transport) else transport.request
        self.registry=registry.rstrip('/')
        self.repository=repository
        self.registry_origin=origin(self.registry)
        self.token=None
        self.calls=[]
    def _manifest_url(self,ref):return '%s/v2/%s/manifests/%s'%(self.registry,self.repository,ref)
    def request(self,method,url,headers=None):
        """单次请求;仅注册表本源附加授权,401 挑战时取短期令牌并重试。"""
        headers=dict(headers or {})
        refreshes=0
        while True:
            out=dict(headers)
            if origin(url)==self.registry_origin and self.token:
                out['Authorization']='Bearer '+self.token
            self.calls.append({'method':method,'url':url,'auth':'Authorization' in out})
            status,h,body=self.transport(method,url,out)
            if status==401 and origin(url)==self.registry_origin and refreshes<MAX_TOKEN_REFRESH:
                challenge=parse_challenge(h.get('www-authenticate'))
                if challenge and challenge.get('realm'):
                    self.token=self._fetch_token(challenge);refreshes+=1;continue
            return status,h,body
    def _fetch_token(self,challenge):
        """令牌端点只收查询参数,不发送授权头。"""
        params={k:v for k,v in challenge.items() if k in ('service','scope') and v}
        url=challenge['realm']+('&' if '?' in challenge['realm'] else '?')+urlencode(params)
        self.calls.append({'method':'GET','url':url,'auth':False})
        status,h,body=self.transport('GET',url,{})
        if status!=200:raise FetchError('令牌获取失败:'+str(status))
        doc=json.loads(body.decode('utf-8'))
        token=doc.get('token') or doc.get('access_token')
        if not token:raise FetchError('令牌响应非法')
        return token
    def _follow(self,method,url,headers=None):
        """手工跟随重定向;跨域时 request 自动不再附加授权。"""
        for _ in range(MAX_REDIRECTS+1):
            status,h,body=self.request(method,url,headers)
            if status in (301,302,303,307,308) and h.get('location'):
                url=urljoin(url,h['location']);continue
            return status,h,body
        raise FetchError('重定向过多')
    def resolve_tag(self,tag):
        """冻结 tag 当前目标:以响应字节算出 digest,与摘要头交叉核对。"""
        status,h,body=self._follow('GET',self._manifest_url(tag),{'Accept':ACCEPT})
        if status!=200:raise FetchError('tag 解析失败:'+str(status))
        computed='sha256:'+hashlib.sha256(body).hexdigest()
        header=(h.get('docker-content-digest') or '').strip()
        if header and header!=computed:raise FetchError('tag 响应与摘要头不符')
        doc=json.loads(body.decode('utf-8'))
        return {'digest':computed,'body':body,'kind':classify(doc,h.get('content-type')),'media_type':mime_of(doc,h.get('content-type'))}
    def observe_tag(self,tag):
        """抓取末再次观测 tag 指向;HEAD 不可用时回退 GET 重算。"""
        status,h,body=self._follow('HEAD',self._manifest_url(tag),{'Accept':ACCEPT})
        if status==405:
            status,h,body=self._follow('GET',self._manifest_url(tag),{'Accept':ACCEPT})
            if status!=200:raise FetchError('tag 观测失败:'+str(status))
            return 'sha256:'+hashlib.sha256(body).hexdigest()
        if status!=200:raise FetchError('tag 观测失败:'+str(status))
        digest=(h.get('docker-content-digest') or '').strip()
        if not digest:raise FetchError('观测缺少摘要头')
        return digest
    def fetch_manifest(self,digest,size=None):
        status,h,body=self._follow('GET',self._manifest_url(digest),{'Accept':ACCEPT})
        if status!=200:raise FetchError('清单获取失败:'+str(status))
        try:check(body,digest,size)
        except ValueError as e:raise FetchError(str(e)) from e
        return body,mime_of(json.loads(body.decode('utf-8')),h.get('content-type'))
    def fetch_blob_to(self,digest,size,path):
        """写入临时片段文件;支持 206 分段续传,完整性与长度由 put_file 最终核验。"""
        url='%s/v2/%s/blobs/%s'%(self.registry,self.repository,digest)
        got=0;errors=0
        with open(path,'wb') as f:
            while True:
                headers={} if got==0 else {'Range':'bytes=%d-'%got}
                try:status,h,body=self._follow('GET',url,headers)
                except OSError:
                    errors+=1
                    if errors>BLOB_RETRIES:raise
                    continue
                if status==200:
                    if got:f.seek(0);f.truncate(0)
                    f.write(body);got=len(body);return
                if status==206:
                    start,end,total=parse_content_range(h.get('content-range'))
                    if start!=got:raise FetchError('分段起点不符')
                    if len(body)!=end-start+1:raise FetchError('分段长度不符')
                    if total is None:
                        if size is None:raise FetchError('分段总长未知')
                        total=size
                    if size is not None and total!=size:raise FetchError('分段总长与描述不符')
                    f.write(body);got+=len(body)
                    if got>total:raise FetchError('分段越界')
                    if got==total:return
                    continue
                raise FetchError('blob 获取失败:'+str(status))

def ensure_manifest(client,store,state,digest,size):
    if store.has(digest):return store.get(digest),None
    data,mt=client.fetch_manifest(digest,size)
    store.put(data,digest,size)
    state.mark_verified(digest);state.save()
    return data,mt

def ensure_blob(client,store,state,digest,size):
    if store.has(digest):return
    frag=store.fragment_path(digest)
    client.fetch_blob_to(digest,size,frag)
    store.put_file(frag,digest,size)
    state.mark_verified(digest);state.save()

def walk_platform(client,store,state,desc,plat,depth):
    """单平台闭包:manifest 核验后沿 config/layers 收集 blob;失败只记在本平台。"""
    if depth>MAX_DEPTH:raise ValueError('索引嵌套过深')
    digest=desc.get('digest')
    if not digest:raise ValueError('描述符缺 digest')
    data,mt=ensure_manifest(client,store,state,digest,desc.get('size'))
    plat['manifest_verified']=True
    doc=json.loads(data.decode('utf-8'))
    if classify(doc,mt or desc.get('mediaType'))=='index':
        for child in doc.get('manifests') or []:walk_platform(client,store,state,child,plat,depth+1)
        return
    refs=([doc['config']] if doc.get('config') else [])+list(doc.get('layers') or [])
    if not refs:raise ValueError('清单无 blob 引用')
    for ref in refs:
        bd=ref.get('digest')
        if not bd:raise ValueError('blob 描述符缺 digest')
        entry={'digest':bd,'size':ref.get('size'),'verified':False}
        plat['blobs'].append(entry)
        try:
            ensure_blob(client,store,state,bd,ref.get('size'))
            entry['verified']=True
        except (FetchError,ValueError):
            plat['missing'].append(bd)
    if plat['missing']:raise FetchError('平台 blob 未齐')

def fetch_closure(client,store,state,tag):
    """冻结 tag 目标并抓全闭包;返回本轮报告。中断后用同一 state 续抓。"""
    store.discard_fragments()
    resumed=bool(state.data.get('frozen'))
    if resumed:
        frozen=state.data['frozen']
        head_bytes,_=ensure_manifest(client,store,state,frozen,None)
        doc=json.loads(head_bytes.decode('utf-8'))
        kind=classify(doc);media_type=mime_of(doc)
    else:
        head=client.resolve_tag(tag)
        frozen=head['digest'];head_bytes=head['body'];kind=head['kind'];media_type=head['media_type']
        state.data['frozen']=frozen
        state.observe(frozen)
        store.put(head_bytes,frozen)
        state.mark_verified(frozen);state.save()
    top=json.loads(head_bytes.decode('utf-8'))
    if kind=='index':
        children=top.get('manifests') or []
        if not children:raise ValueError('索引无平台条目')
    else:
        children=[{'digest':frozen,'size':len(head_bytes),'mediaType':media_type,'platform':None}]
    platforms=[]
    for desc in children:
        plat={'digest':desc.get('digest'),'platform':desc.get('platform'),'manifest_verified':False,'blobs':[],'missing':[],'status':'complete','error':None}
        try:walk_platform(client,store,state,desc,plat,0)
        except (FetchError,ValueError) as e:
            plat['status']='failed';plat['error']=str(e)
        platforms.append(plat);state.save()
    final=client.observe_tag(tag)
    state.observe(final);state.data['final']=final;state.save()
    return {'tag':tag,'repository':client.repository,'frozen_digest':frozen,'final_observation':final,
            'tag_moved':final!=frozen,'resumed':resumed,'observations':list(state.data['observations']),
            'media_type':media_type,'platforms':platforms,
            'deliverable':all(p['status']=='complete' for p in platforms),
            'verified_objects':len(state.data['verified']),'requests':len(client.calls),
            'auth_foreign':sum(1 for c in client.calls if c['auth'] and origin(c['url'])!=client.registry_origin)}

def records_document(report,collection,source_url):
    """闭包报告转本领域记录文档,供 journal 接收与投影评估。"""
    frozen=report['frozen_digest']
    records=[
        {'kind':'tag','id':report['tag'],'version':frozen,'refs':[frozen],
         'payload':{'source_url':source_url,'tag_moved':report['tag_moved'],
                    'final_observation':report['final_observation'],
                    'observations':report['observations'],'deliverable':report['deliverable']}},
        {'kind':'manifest','id':frozen,'version':'received','refs':[],
         'payload':{'media_type':report['media_type']}}]
    for p in report['platforms']:
        records.append({'kind':'platform','id':p['digest'],'version':frozen,'refs':[frozen],
                        'payload':{'platform':p.get('platform'),'manifest_downloaded':bool(p.get('manifest_verified'))}})
        for b in p.get('blobs',[]):
            records.append({'kind':'blob','id':b['digest'],'version':p['digest'],'refs':[p['digest']],
                            'payload':{'downloaded':bool(b.get('verified')),'size':b.get('size')}})
    return {'protocol':'oci-registry','collection':collection,'records':records}

def emit_source_file(report,collection,source_url,path):
    """把记录文档冻结为本地资产,返回可交给 journal.receive 的清单。"""
    raw=json.dumps(records_document(report,collection,source_url),ensure_ascii=False,sort_keys=True).encode('utf-8')
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    return {'kind':'source-records','protocol':'oci-registry','collection':collection,'source_url':source_url,
            'assets':[{'path':p.name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}],'calls':[]}
