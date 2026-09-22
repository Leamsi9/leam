from test_api import login
from test_artifacts import client_for
from test_attachments import upload
from test_resources import publish


def test_http_groups_types_sort_and_cross_group_cursor(tmp_path):
    with client_for(tmp_path) as c:
        login(c)
        assert publish(c,'site',kind='html',content='<h1>Hello</h1>',title='Site').status_code==200
        assert publish(c,'notes',kind='text',content='Notes',title='Notes').status_code==200
        assert upload(c,b'Upload',name='upload.txt').status_code==201
        generated=c.get('/api/artifacts',params=dict(group='generated',sort='type',order='asc')).json()
        assert [r['id'] for r in generated['items']]==['notes','site']
        reverse=c.get('/api/artifacts',params=dict(group='generated',sort='type',order='desc')).json()
        assert [r['id'] for r in reverse['items']]==['site','notes']
        uploads=c.get('/api/artifacts',params=dict(group='uploads',kind='text',sort='type')).json()
        assert len(uploads['items'])==1 and uploads['items'][0]['category']=='attachment'
        assert c.get('/api/artifacts',params=dict(group='generated',kind='html')).json()['items'][0]['id']=='site'
        assert c.get('/api/artifacts',params=dict(group='uploads',kind='html')).json()['items']==[]
        first=c.get('/api/artifacts',params=dict(group='generated',sort='type',order='asc',limit=1)).json()
        cursor=first['nextCursor'];assert cursor
        second=c.get('/api/artifacts',params=dict(group='generated',sort='type',order='asc',limit=1,cursor=cursor)).json()
        assert second['items'][0]['id']=='site'
        assert c.get('/api/artifacts',params=dict(group='uploads',sort='type',order='asc',cursor=cursor)).status_code==422
        assert c.get('/api/artifacts',params=dict(group='unknown')).status_code==422
