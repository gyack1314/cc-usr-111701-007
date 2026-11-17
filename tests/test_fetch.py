"""OCI 闭包抓取:冻结、核验、授权域、平台隔离、tag 移动与断点续抓。"""
import hashlib,json,tempfile,unittest
from pathlib import Path
from capture_data.journal import connect,receive
from capture_data.oci import RegistryClient,fetch_closure,emit_source_file,records_document
from capture_data.projection import overview
from capture_data.store import ObjectStore,FetchState,check
from tests.oci_fake import FakeWorld

def build_world():
    w=FakeWorld()
    shared=w.add_blob(b'shared-layer-payload')
    la=w.add_blob(b'layer-a-payload');lb=w.add_blob(b'layer-b-payload')
    ma,ba=w.add_image('amd64',[shared,la])
    mb,bb=w.add_image('arm64',[shared,lb])
    idx=w.add_index([(ma,{'os':'linux','architecture':'amd64'}),(mb,{'os':'linux','architecture':'arm64'})])
    w.tags['stable']=idx
    return w,idx,(ma,mb),(shared,la,lb),(ba,bb)

class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def run_fetch(self,w):
        client=RegistryClient(w.transport,w.registry,w.repo)
        store=ObjectStore(self.root/'cache');state=FetchState(self.root/'cache'/'state.json')
        return client,store,state,fetch_closure(client,store,state,'stable')
    def test_closure_freeze_shared_cache_and_auth_domain(self):
        w,idx,(ma,mb),(shared,la,lb),_=build_world()
        w.redirect_blobs=True
        client,store,state,report=self.run_fetch(w)
        self.assertTrue(report['deliverable'])
        self.assertFalse(report['tag_moved'])
        self.assertEqual(report['frozen_digest'],idx)
        self.assertEqual([p['status'] for p in report['platforms']],['complete','complete'])
        self.assertEqual(report['auth_foreign'],0)
        for d in (shared,la,lb):self.assertTrue(store.has(d))
        # 共享 blob 跨平台只抓一次
        cdn_shared=[r for r in w.requests if r['url'].startswith(w.cdn) and shared in r['url']]
        self.assertEqual(len(cdn_shared),1)
        # 授权头不出注册表域:CDN 与令牌端点均无 Authorization
        self.assertTrue(any(r['url'].startswith(w.cdn) for r in w.requests))
        for r in w.requests:
            if not r['url'].startswith(w.registry):
                self.assertNotIn('authorization',r['headers'])
        self.assertTrue(any(r['headers'].get('authorization','').startswith('Bearer ') for r in w.requests if r['url'].startswith(w.registry)))
        # 记录文档入账本后可发布,重复接收幂等
        manifest=emit_source_file(report,'c1','https://registry.example.test/v2/team/app/manifests/stable',self.root/'out'/'closure-source.json')
        db=connect(':memory:')
        first=receive(db,manifest,self.root/'out')
        self.assertEqual(receive(db,manifest,self.root/'out'),first)
        view=overview(db,'c1')
        self.assertTrue(view['locally_closed'])
        self.assertTrue(view['business']['publishable'])
        self.assertEqual(view['business']['platform_count'],2)
        db.close()
    def test_platform_failure_not_masked(self):
        w,idx,(ma,mb),(shared,la,lb),_=build_world()
        w.corrupt[la]=b'corrupted-bytes'
        client,store,state,report=self.run_fetch(w)
        self.assertFalse(report['deliverable'])
        by={p['digest']:p for p in report['platforms']}
        self.assertEqual(by[ma]['status'],'failed')
        self.assertEqual(by[mb]['status'],'complete')
        self.assertIn(la,by[ma]['missing'])
        self.assertFalse(store.has(la))
        self.assertTrue(store.has(lb))
        manifest=emit_source_file(report,'c2','u',self.root/'out'/'doc.json')
        db=connect(':memory:');receive(db,manifest,self.root/'out')
        view=overview(db,'c2')
        self.assertFalse(view['business']['publishable'])
        self.assertTrue(any(la in r for r in view['business']['reasons']))
        db.close()
    def test_descriptor_size_guard(self):
        w,idx,(ma,mb),_,_=build_world()
        wrong=len(w.manifests[mb][1])+1
        w.tags['stable']=w.add_index([(ma,{'os':'linux','architecture':'amd64'}),(mb,{'os':'linux','architecture':'arm64'})],
                                     sizes=[len(w.manifests[ma][1]),wrong])
        client,store,state,report=self.run_fetch(w)
        by={p['digest']:p for p in report['platforms']}
        self.assertEqual(by[mb]['status'],'failed')
        self.assertEqual(by[ma]['status'],'complete')
        self.assertFalse(report['deliverable'])
    def test_check_digest_and_size(self):
        d='sha256:'+hashlib.sha256(b'x').hexdigest()
        check(b'x',d);check(b'x',d,1)
        with self.assertRaises(ValueError):check(b'x',d,2)
        with self.assertRaises(ValueError):check(b'y',d)
        with self.assertRaises(ValueError):check(b'x','md5:abc')
    def test_tag_moved_keeps_both_observations(self):
        w,idx1,(ma,mb),_,_=build_world()
        mc,_=w.add_image('v2',[w.add_blob(b'layer-c')])
        idx2=w.add_index([(mc,{'os':'linux','architecture':'amd64'})])
        w.pending_tag=('stable',idx2)
        client,store,state,report=self.run_fetch(w)
        self.assertTrue(report['tag_moved'])
        self.assertEqual(report['frozen_digest'],idx1)
        self.assertEqual(report['final_observation'],idx2)
        self.assertEqual(report['observations'],[idx1,idx2])
        self.assertTrue(report['deliverable'])
        saved=json.loads((self.root/'cache'/'state.json').read_text('utf-8'))
        self.assertEqual(saved['observations'],[idx1,idx2])
        doc=records_document(report,'c3','u')
        tag_row=[r for r in doc['records'] if r['kind']=='tag'][0]
        self.assertEqual(tag_row['version'],idx1)
        self.assertEqual(tag_row['payload']['final_observation'],idx2)
    def test_ranged_blob_and_head_fallback(self):
        w=FakeWorld()
        big=w.add_blob(bytes(range(256))*4)
        m,blobs=w.add_image('solo',[big])
        w.tags['stable']=m
        w.range_only.add(big)
        w.chunk_size=100
        w.head_supported=False
        client,store,state,report=self.run_fetch(w)
        self.assertTrue(report['deliverable'])
        self.assertFalse(report['tag_moved'])
        self.assertEqual(len(report['platforms']),1)
        self.assertEqual(report['platforms'][0]['digest'],m)
        self.assertTrue(store.has(big))
        ranged=[r for r in w.requests if big in r['url'] and 'range' in r['headers']]
        self.assertGreaterEqual(len(ranged),10)
    def test_short_lived_tokens_refresh(self):
        w,idx,(ma,mb),(shared,la,lb),_=build_world()
        w.one_shot_tokens=True
        client,store,state,report=self.run_fetch(w)
        self.assertTrue(report['deliverable'])
        token_calls=[r for r in w.requests if r['url'].startswith(w.auth)]
        self.assertGreaterEqual(len(token_calls),3)
        for r in token_calls:self.assertNotIn('authorization',r['headers'])
        self.assertEqual(report['auth_foreign'],0)
    def test_resume_trusts_only_verified(self):
        w,idx,(ma,mb),(shared,la,lb),(ba,bb)=build_world()
        w.fail_blobs[shared]=10
        client=RegistryClient(w.transport,w.registry,w.repo)
        store=ObjectStore(self.root/'cache');state=FetchState(self.root/'cache'/'state.json')
        with self.assertRaises(ConnectionError):
            fetch_closure(client,store,state,'stable')
        self.assertTrue(store.has(idx))
        saved=json.loads((self.root/'cache'/'state.json').read_text('utf-8'))
        self.assertEqual(saved['frozen'],idx)
        self.assertIn(idx,saved['verified'])
        # 服务恢复;植入垃圾片段,并篡改一个已核验对象
        w.fail_blobs.clear()
        cfg_a=ba[0]
        store.path_of(cfg_a).write_bytes(b'tampered')
        garbage=store.fragment_path(la);garbage.write_bytes(b'garbage-fragment')
        mark=len(w.requests)
        client2=RegistryClient(w.transport,w.registry,w.repo)
        store2=ObjectStore(self.root/'cache');state2=FetchState(self.root/'cache'/'state.json')
        report=fetch_closure(client2,store2,state2,'stable')
        r2=w.requests[mark:]
        self.assertTrue(report['resumed'])
        self.assertTrue(report['deliverable'])
        # 冻结目标不重新解析;已核验 manifest 不重新抓取
        self.assertEqual([r for r in r2 if r['method']=='GET' and r['url'].endswith('/manifests/stable')],[])
        self.assertEqual([r for r in r2 if '/manifests/' in r['url'] and r['url'].rsplit('/',1)[-1] in (idx,ma)],[])
        # 本轮只补缺失与被篡改的对象
        fetched={r['url'].rsplit('/',1)[-1] for r in r2 if r['method']=='GET' and '/blobs/' in r['url']}
        self.assertEqual(fetched,{shared,la,lb,cfg_a,bb[0]})
        # 垃圾片段被弃,la 内容来自注册表而非片段
        self.assertFalse(garbage.exists())
        self.assertEqual(store2.get(la),b'layer-a-payload')
        self.assertEqual(store2.get(cfg_a),json.dumps({'config':'amd64'},sort_keys=True).encode())
        self.assertEqual(store2.get(shared),b'shared-layer-payload')

if __name__=='__main__':unittest.main()
