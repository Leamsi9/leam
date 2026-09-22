"""Exercise middleware at the actual authenticated publication boundary."""
from fastapi.testclient import TestClient
from test_api import FakeCodex, login
from leam_api.app import create_app


def test_resources_large_envelope_is_authenticated_and_origin_bound(tmp_path):
    with TestClient(create_app(tmp_path, origins={'http://testserver'}, bootstrap='bootstrap-for-tests', codex=FakeCodex())) as client:
        assert client.post('/api/artifacts', content=b'x' * (2 * 1024 * 1024)).status_code == 401
        login(client)
        body = {'id': 'large-text', 'title': 'Large text', 'kind': 'text', 'content': 'x' * (2 * 1024 * 1024)}
        assert client.post('/api/artifacts', json=body).status_code == 403
        response = client.post('/api/artifacts', json=body, headers={'origin': 'http://testserver'})
        assert response.status_code in (200, 201), response.text
        assert client.get('/api/artifacts/large-text/download').content == body['content'].encode()
        assert client.post('/api/artifacts', content=b'{}', headers={'origin': 'http://testserver', 'content-length': str(15 * 1024 * 1024 + 1)}).status_code == 413
        assert client.post('/api/auth/login', content=b'x' * (1024 * 1024 + 1), headers={'origin': 'http://testserver'}).status_code == 413
