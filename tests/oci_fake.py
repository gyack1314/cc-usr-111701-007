"""合成三域传输:注册表(Bearer 挑战)、令牌端点、CDN 下载域;不访问真实站点。"""
import hashlib,json,re
from urllib.parse import urlsplit

INDEX_MT='application/vnd.oci.image.index.v1+json'
MANIFEST_MT='application/vnd.oci.image.manifest.v1+json'
CONFIG_MT='application/vnd.oci.image.config.v1+json'
LAYER_MT='application/vnd.oci.image.layer.v1.tar'

def digest_of(data):return 'sha256:'+hashlib.sha256(data).hexdigest()

class FakeWorld:
    """可注入故障的合成注册表环境;requests 记录每次请求的方法、地址与头。"""
    def __init__(self,repo='team/app'):
        self.repo=repo
        self.registry='https://registry.example.test'
        self.auth='https://auth.example.test'
        self.cdn='https://cdn.example.test'
        self.manifests={}
        self.blobs={}
        self.tags={}
        self.requests=[]
        self.challenge=True
        self.one_shot_tokens=False
        self.redirect_blobs=False
        self.range_only=set()
        self.chunk_size=8
        self.corrupt={}
        self.pending_tag=None
        self.head_supported=True
        self.fail_blobs={}
        self._tokens={}
        self._issued=0
    def add_blob(self,data):
        d=digest_of(data);self.blobs[d]=data;return d
    def add_manifest_doc(self,doc,media_type=MANIFEST_MT):
        raw=json.dumps(doc,sort_keys=True).encode('utf-8')
        d=digest_of(raw);self.manifests[d]=(media_type,raw);return d
    def add_image(self,seed,layer_digests):
        cfg=self.add_blob(json.dumps({'config':seed},sort_keys=True).encode())
        doc={'schemaVersion':2,'mediaType':MANIFEST_MT,
             'config':{'mediaType':CONFIG_MT,'digest':cfg,'size':len(self.blobs[cfg])},
             'layers':[{'mediaType':LAYER_MT,'digest':d,'size':len(self.blobs[d])} for d in layer_digests]}
        return self.add_manifest_doc(doc),[cfg]+list(layer_digests)
    def add_index(self,entries,sizes=None):
        rows=[]
        for i,(d,platform) in enumerate(entries):
            size=sizes[i] if sizes else len(self.manifests[d][1])
            rows.append({'mediaType':MANIFEST_MT,'digest':d,'size':size,'platform':platform})
        return self.add_manifest_doc({'schemaVersion':2,'mediaType':INDEX_MT,'manifests':rows},INDEX_MT)
    def transport(self,method,url,headers=None):
        headers={k.lower():v for k,v in dict(headers or {}).items()}
        self.requests.append({'method':method,'url':url,'headers':headers})
        host=urlsplit(url).hostname
        if host==urlsplit(self.auth).hostname:return self._token(url)
        if host==urlsplit(self.cdn).hostname:return self._cdn(method,url,headers)
        if host==urlsplit(self.registry).hostname:return self._registry(method,url,headers)
        raise AssertionError('未知域:'+url)
    def _token(self,url):
        self._issued+=1
        tok='token-%d'%self._issued
        self._tokens[tok]=1 if self.one_shot_tokens else 1<<30
        return 200,{'content-type':'application/json'},json.dumps({'token':tok,'expires_in':60}).encode()
    def _auth_ok(self,headers):
        if not self.challenge:return True
        auth=headers.get('authorization','')
        tok=auth[len('Bearer '):] if auth.startswith('Bearer ') else None
        if tok and self._tokens.get(tok,0)>0:
            self._tokens[tok]-=1;return True
        return False
    def _challenge(self):
        return 401,{'www-authenticate':'Bearer realm="%s/token",service="registry",scope="repository:%s:pull"'%(self.auth,self.repo)},b''
    def _registry(self,method,url,headers):
        if not self._auth_ok(headers):return self._challenge()
        path=urlsplit(url).path
        prefix='/v2/%s/'%self.repo
        if path.startswith(prefix+'manifests/'):
            return self._manifest(method,path[len(prefix+'manifests/'):])
        if path.startswith(prefix+'blobs/'):
            d=path[len(prefix+'blobs/'):]
            if self.redirect_blobs:return 302,{'location':'%s/blobs/%s'%(self.cdn,d)},b''
            return self._blob(d,headers)
        return 404,{},b''
    def _manifest(self,method,ref):
        d=self.tags.get(ref,ref)
        if d not in self.manifests:return 404,{},b''
        mt,raw=self.manifests[d]
        raw=self.corrupt.get(d,raw)
        head={'content-type':mt,'docker-content-digest':d}
        if method=='HEAD':
            if not self.head_supported:return 405,{},b''
            return 200,head,b''
        if method!='GET':return 405,{},b''
        if ref in self.tags and self.pending_tag and self.pending_tag[0]==ref:
            self.tags[ref]=self.pending_tag[1];self.pending_tag=None
        return 200,head,raw
    def _cdn(self,method,url,headers):
        return self._blob(urlsplit(url).path.rsplit('/',1)[-1],headers)
    def _blob(self,d,headers):
        if self.fail_blobs.get(d,0)>0:
            self.fail_blobs[d]-=1
            raise ConnectionError('模拟连接中断')
        if d not in self.blobs:return 404,{},b''
        raw=self.corrupt.get(d,self.blobs[d])
        rng=headers.get('range')
        if rng or d in self.range_only:
            start=0
            if rng:
                m=re.fullmatch(r'bytes=(\d+)-',rng)
                if not m:return 416,{},b''
                start=int(m.group(1))
            if start>=len(raw):return 416,{},b''
            end=min(start+self.chunk_size,len(raw))
            return 206,{'content-range':'bytes %d-%d/%d'%(start,end-1,len(raw))},raw[start:end]
        return 200,{'content-length':str(len(raw))},raw
