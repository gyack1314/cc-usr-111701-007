"""索引引用已有，不代表各平台 blob 已到齐。"""
def assess(rows):
    absent=sorted({r['id'] for r in rows if r['kind']=='blob' and not r['payload'].get('downloaded')})
    pending=sorted({r['id'] for r in rows if r['kind']=='platform' and not r['payload'].get('manifest_downloaded')})
    return {'publishable':not absent and not pending,'reasons':['平台 manifest 未核验:'+x for x in pending]+['blob 未核验:'+x for x in absent],
            'platform_count':len([r for r in rows if r['kind']=='platform'])}
