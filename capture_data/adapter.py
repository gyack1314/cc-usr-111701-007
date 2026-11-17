import json
def decode(raw, manifest):
    doc=json.loads(raw.decode('utf-8'))
    if doc['mediaType']!='application/vnd.oci.image.index.v1+json':raise ValueError('索引类型非法')
    tag=doc['tag'];index=doc['digest']
    records=[{'kind':'tag','id':tag,'version':index,'refs':[index],
              'payload':{'source_url':manifest['source_url']}},
             {'kind':'manifest','id':index,'version':'received','refs':[],
              'payload':{'media_type':doc['mediaType']}}]
    for entry in doc['manifests']:
        child=entry['digest'];blobs=[entry['config']]+entry['layers']
        records.append({'kind':'platform','id':child,'version':index,'refs':[index]+blobs,
                        'payload':entry['platform']})
        for blob in blobs:
            records.append({'kind':'blob','id':blob,'version':child,'refs':[child],
                            'payload':{'downloaded':False}})
    return {'protocol':manifest['protocol'],'collection':manifest['collection'],'records':records}
