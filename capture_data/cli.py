"""查看已接收资料的身份与本地引用;或按 tag 抓取 OCI 闭包。"""
import argparse,json
from pathlib import Path
from .io import load
from .domain import summarize
from .journal import connect,receive
from .projection import overview
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 i=sub.add_parser('inspect');i.add_argument('file')
 f=sub.add_parser('fetch')
 f.add_argument('--registry',required=True);f.add_argument('--repo',required=True);f.add_argument('--tag',required=True)
 f.add_argument('--collection',required=True);f.add_argument('--cache',required=True);f.add_argument('--journal',required=True)
 a=p.parse_args()
 if a.command=='inspect':
  path=Path(a.file);v,digest=load(path);summary=summarize(v,path.parent)
  c=connect(':memory:');source=receive(c,v,path.parent)
  print(json.dumps({'manifest_sha256':digest,'source_sha256':source,'summary':summary,'overview':overview(c,v['collection'])},ensure_ascii=False,indent=2));return
 from .http import HttpTransport
 from .oci import RegistryClient,fetch_closure,emit_source_file
 from .store import ObjectStore,FetchState
 cache=Path(a.cache)
 client=RegistryClient(HttpTransport(),a.registry,a.repo)
 store=ObjectStore(cache);state=FetchState(cache/'fetch-state.json')
 report=fetch_closure(client,store,state,a.tag)
 source_url='%s/v2/%s/manifests/%s'%(a.registry,a.repo,a.tag)
 manifest=emit_source_file(report,a.collection,source_url,cache/'closure-source.json')
 db=connect(a.journal);digest=receive(db,manifest,cache)
 print(json.dumps({'source_sha256':digest,'report':report,'overview':overview(db,a.collection)},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
