"""Hierarchy migration, manual editing, and folded-folder fulltext scope."""
import json
import sqlite3

from server.store import SCHEMA, Store
from server.tests.test_pipeline import workbench, upload, process, html_page, LOCAL


def path(level1='artificial_intelligence', second='机器学习', third='模型训练'):
    return {'level1Id': level1, 'level2': second, 'level3': third}


def test_existing_schema_migrates_without_overwriting_manual_metadata(tmp_path):
    database=tmp_path/'library.sqlite3'
    old_schema=SCHEMA.replace(",\n classification_paths_json TEXT NOT NULL DEFAULT '[]'", '')
    with sqlite3.connect(database) as db:
        db.executescript(old_schema)
        db.execute("INSERT INTO sources(id,identity,created_at,updated_at,tags_json,tags_manual,favorite) VALUES('old','file:old','a','b','[\"个人主题\"]',1,1)")
    store=Store(tmp_path)
    Store(tmp_path)  # migration is safe when services both initialize
    with store.connect() as db:
        row=db.execute('SELECT * FROM sources').fetchone()
        assert row['tags_json']=='["个人主题"]'
        assert row['tags_manual']==row['favorite']==1
        assert row['classification_paths_json']=='[]'


def test_manual_paths_replace_only_derived_tags_and_keep_all_three_levels(workbench):
    client,store,_=workbench
    source=process(store,upload(client))['source_id']
    store.patch(source,{'tags':['个人收藏']})
    response=client.patch('/api/documents/'+source,json={'classificationPaths':[path(),path('cybersecurity','模型安全','对抗攻击')]},headers=LOCAL)
    assert response.status_code==200,response.text
    doc=response.json()['document']
    assert doc['classificationManual'] is True
    assert len(doc['classificationPaths'])==2
    assert doc['tags']==['人工智能','机器学习','模型训练','网络安全','模型安全','对抗攻击','个人收藏']
    doc=store.patch(source,{'classificationPaths':[path(third='模型评估')]})
    assert doc['tags']==['人工智能','机器学习','模型评估','个人收藏']
    doc=store.patch(source,{'tags':['新辅助标签']})
    assert doc['tags']==['人工智能','机器学习','模型评估','新辅助标签']
    assert Store(store.root).detail(source)['classificationPaths']==doc['classificationPaths']
    assert store.documents()['documents'][0]['classificationPaths']==doc['classificationPaths']
    cleared=store.patch(source,{'classificationPaths':[]})
    assert cleared['tags']==['新辅助标签'] and cleared['classificationManual']


def test_invalid_manual_path_never_partially_updates_flags_or_tags(workbench):
    client,store,_=workbench
    source=process(store,upload(client))['source_id']
    before=store.detail(source)
    for paths in ([path('invented')],[path()]*4,[path(third='机器学习')],[{'nodes':[]}], [path(),path()]):
        response=client.patch('/api/documents/'+source,json={'classificationPaths':paths,'favorite':True,'tags':['错误']},headers=LOCAL)
        assert response.status_code in (400,422),response.text
        assert store.detail(source)==before


def test_scoped_search_is_exact_deduplicated_paged_and_keeps_fulltext(workbench):
    client,store,_=workbench
    first=process(store,upload(client,html_page('甲')))['source_id']
    second=process(store,upload(client,html_page('乙')))['source_id']
    store.patch(first,{'classificationPaths':[path()], 'favorite':True})
    payload={'q':'基层档案治理','category':'uncat:some-parent','sourceIds':[first,first],'limit':1}
    result=client.post('/api/search',json=payload,headers=LOCAL)
    assert result.status_code==200,result.text
    data=result.json()
    assert data['total']==1 and data['results'][0]['id']==first
    assert data['results'][0]['matchBlockId']
    assert data['results'][0]['classificationPaths']
    assert client.post('/api/search',json={**payload,'offset':1},headers=LOCAL).json()['results']==[]
    assert client.post('/api/search',json={**payload,'sourceIds':[]},headers=LOCAL).json()['total']==0
    assert client.post('/api/search',json={**payload,'sourceIds':[second],'filter':'favorite'},headers=LOCAL).json()['total']==0
    assert client.get('/api/search',params={'q':'基层档案治理','category':'机器学习'}).json()['total']==1
