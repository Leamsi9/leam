import pytest
from fastapi.testclient import TestClient
from test_api import FakeCodex, login

from leam_api.app import create_app
from leam_api.artifacts import MAX_BYTES, PREVIEW_POLICY, Artifacts
from leam_api.store import Store


def client_for(path):
    return TestClient(create_app(path, origins={'http://testserver'}, bootstrap='bootstrap-for-tests', codex=FakeCodex()))


def test_published_report_requires_login_and_remains_downloadable(tmp_path):
    Artifacts(Store(tmp_path)).publish('review-v1', 'Review', 'markdown', '# Private review')
    with client_for(tmp_path) as client:
        for suffix in ['', '/download', '/preview']:
            assert client.get('/api/artifacts/review-v1' + suffix).status_code == 401
        login(client)
        result = client.get('/api/artifacts/review-v1')
        assert result.status_code == 200
        assert result.json()['content'] == '# Private review'
        assert result.headers['cache-control'] == 'no-store'
        download = client.get('/api/artifacts/review-v1/download')
        assert download.content == b'# Private review'
        assert download.headers['content-disposition'] == 'attachment; filename="review-v1.md"'
        assert download.headers['content-security-policy'] == "default-src 'none'; sandbox"
        assert client.get('/api/artifacts/review-v1/preview').status_code == 404
        client.post('/api/auth/logout', headers={'origin': 'http://testserver'})
        assert client.get('/api/artifacts/review-v1').status_code == 401


def test_html_preview_has_specific_sandbox_without_weakening_other_routes(tmp_path):
    Artifacts(Store(tmp_path)).publish('study-v1', 'Study', 'html', '<script>window.example=1</script>')
    with client_for(tmp_path) as client:
        login(client)
        assert 'content' not in client.get('/api/artifacts/study-v1').json()
        preview = client.get('/api/artifacts/study-v1/preview')
        assert preview.status_code == 200
        assert preview.headers['content-security-policy'] == PREVIEW_POLICY
        assert preview.headers['x-frame-options'] == 'SAMEORIGIN'
        assert preview.headers['cache-control'] == 'no-store'
        assert 'allow-same-origin' not in PREVIEW_POLICY
        auth = client.get('/api/auth/status')
        assert auth.headers['x-frame-options'] == 'DENY'
        assert 'script-src \'self\'' in auth.headers['content-security-policy']
        missing = client.get('/api/artifacts/missing/preview')
        assert missing.status_code == 404
        assert missing.headers['x-frame-options'] == 'DENY'


def test_immutable_publication_and_no_path_lookup(tmp_path):
    artifacts = Artifacts(Store(tmp_path))
    first = artifacts.publish('report', 'Report', 'markdown', 'Hello')
    assert artifacts.publish('report', 'Report', 'markdown', 'Hello') == first
    with pytest.raises(ValueError, match='already published'):
        artifacts.publish('report', 'Report', 'markdown', 'Changed')
    with client_for(tmp_path) as client:
        login(client)
        for key in ['missing', '%2E%2E%2Fleam.sqlite3', '%2Fetc%2Fpasswd', 'A', 'x' * 97]:
            assert client.get('/api/artifacts/' + key).status_code == 404
        assert client.get('/api/artifacts/report').json()['content'] == 'Hello'
        assert client.post('/api/artifacts/report', json={'path': '/etc/passwd'}, headers={'origin': 'http://testserver'}).status_code == 405


@pytest.mark.parametrize('key,title,kind,content', [('../x', 'X', 'markdown', 'ok'), ('x', '', 'markdown', 'ok'), ('x', 'X\nY', 'markdown', 'ok'), ('x', 'X', 'pdf', 'ok'), ('x', 'X', 'markdown', ''), ('x', 'X', 'html', '\0'), ('x', 'X', 'markdown', 'x' * (MAX_BYTES + 1))])
def test_publication_rejects_invalid_input(tmp_path, key, title, kind, content):
    with pytest.raises(ValueError):
        Artifacts(Store(tmp_path)).publish(key, title, kind, content)


def test_installation_isolation_and_restart(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    Artifacts(Store(first)).publish('report', 'Report', 'markdown', 'Private first account')
    with client_for(second) as client:
        login(client)
        assert client.get('/api/artifacts/report').status_code == 404
    with client_for(first) as client:
        login(client)
        assert client.get('/api/artifacts/report').json()['content'] == 'Private first account'
