"""索引引用已有，不代表各平台 blob 已到齐。"""
def assess(rows):
    absent=sorted({r['id'] for r in rows if r['kind']=='blob' and not r['payload'].get('downloaded')})
    return {'publishable':not absent,'reasons':['blob 未核验:'+x for x in absent],
            'platform_count':len([r for r in rows if r['kind']=='platform'])}
