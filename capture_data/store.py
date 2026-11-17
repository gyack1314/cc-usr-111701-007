"""已核验内容寻址缓存与断点状态;临时片段不属于可恢复内容。"""
import hashlib,json,os,tempfile
from pathlib import Path

def digest_hex(digest):
    algo,_,hexv=(digest or '').partition(':')
    if algo!='sha256' or len(hexv)!=64 or any(c not in '0123456789abcdef' for c in hexv):raise ValueError('摘要算法非法')
    return hexv

def check(data,digest,size=None):
    """身份只认 digest 与长度。"""
    if hashlib.sha256(data).hexdigest()!=digest_hex(digest):raise ValueError('摘要不符')
    if size is not None and len(data)!=size:raise ValueError('长度不符')

class ObjectStore:
    """只存放已通过 digest 与长度核验的字节;写入原子生效,读取复核,损坏即弃。"""
    def __init__(self,root):
        self.root=Path(root)
        self.objects=self.root/'objects'/'sha256'
        self.fragments=self.root/'fragments'
        self.objects.mkdir(parents=True,exist_ok=True)
        self.fragments.mkdir(parents=True,exist_ok=True)
    def path_of(self,digest):return self.objects/digest_hex(digest)
    def has(self,digest):
        p=self.path_of(digest)
        if not p.exists():return False
        try:check(p.read_bytes(),digest)
        except ValueError:
            p.unlink();return False
        return True
    def get(self,digest):
        if not self.has(digest):raise KeyError(digest)
        return self.path_of(digest).read_bytes()
    def put(self,data,digest,size=None):
        check(data,digest,size)
        fd,tmp=tempfile.mkstemp(dir=str(self.objects))
        try:
            with os.fdopen(fd,'wb') as f:f.write(data)
            os.replace(tmp,self.path_of(digest))
        except BaseException:
            try:os.unlink(tmp)
            except OSError:pass
            raise
    def put_file(self,src,digest,size=None):
        """流式核验后原子收编;未通过核验时片段原地保留,下轮开始即弃。"""
        n=0;h=hashlib.sha256()
        with open(src,'rb') as f:
            for chunk in iter(lambda:f.read(1<<20),b''):h.update(chunk);n+=len(chunk)
        if size is not None and n!=size:raise ValueError('长度不符')
        if h.hexdigest()!=digest_hex(digest):raise ValueError('摘要不符')
        os.replace(src,self.path_of(digest))
    def fragment_path(self,digest):return self.fragments/(digest_hex(digest)+'.part')
    def discard_fragments(self):
        for p in self.fragments.glob('*.part'):p.unlink()

class FetchState:
    """一轮抓取的持久状态:冻结目标、tag 观测序列与已核验对象;不含任何未核验字节。"""
    def __init__(self,path):
        self.path=Path(path)
        self.data={'frozen':None,'final':None,'observations':[],'verified':[]}
        if self.path.exists():
            v=json.loads(self.path.read_text('utf-8'))
            if not isinstance(v,dict):raise ValueError('状态文件非法')
            for k in ('observations','verified'):
                if k in v and not isinstance(v[k],list):raise ValueError('状态文件非法')
            for k in self.data:
                if k in v:self.data[k]=v[k]
    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(dir=str(self.path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(self.data,f,ensure_ascii=False,sort_keys=True)
            os.replace(tmp,self.path)
        except BaseException:
            try:os.unlink(tmp)
            except OSError:pass
            raise
    def observe(self,digest):
        if not self.data['observations'] or self.data['observations'][-1]!=digest:self.data['observations'].append(digest)
    def mark_verified(self,digest):
        if digest not in self.data['verified']:self.data['verified'].append(digest)
