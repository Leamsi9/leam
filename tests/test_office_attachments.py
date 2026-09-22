"""Office coverage through authenticated upload and the actual dispatch callers."""

import base64
import io
import json
import struct
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
import pytest
from fastapi.testclient import TestClient
from shared_coding_fixtures import SHARED_THREAD
from test_api import FakeCodex, login, make
from test_attachments import ORIGIN, upload
from test_shared_coding_api import api_owner

from leam_api.app import create_app
from leam_api.ironclaw import IronClaw
from leam_api.office_documents import OFFICE_TYPES

MIMES = {details[0][1:]: mime for mime, details in OFFICE_TYPES.items()}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def relationships(items):
    return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(
        f'<Relationship Id="{key}" Type="{REL}{kind}" Target="{target}"{extra}/>'
        for key, kind, target, extra in items
    ) + '</Relationships>'


def parts(kind, marker="Synthetic Office evidence"):
    _, main, _, content_type = OFFICE_TYPES[MIMES[kind]]
    result = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/{main}" ContentType="{content_type}"/></Types>',
        '_rels/.rels': relationships([('main', 'officeDocument', main, '')]),
    }
    marker = escape(marker)
    if kind == 'docx':
        result[main] = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + marker + '</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table evidence</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'
        result['word/_rels/document.xml.rels'] = relationships([('link', 'hyperlink', 'https://example.invalid/private', ' TargetMode="External"')])
    elif kind == 'xlsx':
        result[main] = '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="' + REL.rstrip('/') + '"><sheets><sheet name="Budget" sheetId="1" r:id="sheet"/></sheets></workbook>'
        result['xl/_rels/workbook.xml.rels'] = relationships([('sheet', 'worksheet', 'worksheets/sheet1.xml', ''), ('strings', 'sharedStrings', 'sharedStrings.xml', '')])
        result['xl/sharedStrings.xml'] = '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>' + marker + '</t></si></sst>'
        result['xl/worksheets/sheet1.xml'] = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Inline evidence</t></is></c><c r="C1"><f>1+2</f><v>3</v></c><c r="D1"><f>2+2</f></c></row></sheetData></worksheet>'
    else:
        result[main] = '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="' + REL.rstrip('/') + '"><p:sldIdLst><p:sldId id="256" r:id="first"/><p:sldId id="257" r:id="second"/></p:sldIdLst></p:presentation>'
        result['ppt/_rels/presentation.xml.rels'] = relationships([('first', 'slide', 'slides/slide2.xml', ''), ('second', 'slide', 'slides/slide1.xml', '')])
        template = '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><a:p><a:r><a:t>{}</a:t></a:r></a:p></p:cSld></p:sld>'
        result['ppt/slides/slide2.xml'] = template.format(marker)
        result['ppt/slides/slide1.xml'] = template.format('Second slide evidence')
        result['ppt/slides/_rels/slide2.xml.rels'] = relationships([('notes', 'notesSlide', '../notesSlides/notesSlide1.xml', '')])
        result['ppt/notesSlides/notesSlide1.xml'] = template.format('Speaker evidence').replace('p:sld', 'p:notes')
    return result


def archive(items, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=compression) as output:
        for name, value in items.items():
            output.writestr(name, value)
    return buffer.getvalue()


def connect(client):
    response = client.post('/api/codex/threads/t/connect', json={'handoffConfirmed': True}, headers=ORIGIN)
    assert response.status_code == 200, response.text


def send(client, ids, request_id='office-request'):
    return client.post('/api/codex/threads/t/turns', json={'text': 'Read these files', 'requestId': request_id, 'attachmentIds': ids}, headers=ORIGIN)


def documents(codex):
    sent = next(params for method, params in codex.calls if method == 'turn/start')
    assert sent['input'] == [{'type': 'text', 'text': 'Read these files', 'text_elements': []}]
    assert sent['additionalContext']['leam.agent-protocols']['kind'] == 'application'
    attached = sent['additionalContext']['leam.attachments']
    assert attached['kind'] == 'untrusted'
    return json.loads(attached['value'])['documents']


@pytest.mark.parametrize('kind', ['docx', 'xlsx', 'pptx'])
def test_office_upload_coding_context_original_download_and_retry(tmp_path, kind):
    client, codex = make(tmp_path)
    data = archive(parts(kind))
    with client:
        login(client)
        response = upload(client, data, MIMES[kind], 'evidence.' + kind)
        assert response.status_code == 201, response.text
        item = response.json()
        assert client.get('/api/attachments/' + item['id']).content == data
        connect(client)
        first = send(client, [item['id']])
        assert first.status_code == 200, first.text
        assert send(client, [item['id']]).json() == first.json()
        assert sum(method == 'turn/start' for method, _ in codex.calls) == 1
        document = documents(codex)[0]
        assert 'Synthetic Office evidence' in document['text']
        assert document['truncated'] is False
        path = Path(document['localPath'])
        assert path.suffix == '.' + kind and path.read_bytes() == data
        assert path.stat().st_mode & 0o077 == 0
        assert client.delete('/api/attachments/' + item['id'], headers=ORIGIN).status_code == 409
        if kind == 'docx':
            assert 'Table evidence' in document['text']
            assert 'example.invalid' not in document['text']
        elif kind == 'xlsx':
            assert '[Sheet: Budget]' in document['text']
            assert 'B1=Inline evidence' in document['text']
            assert 'C1=3 [cached; formula: =1+2]' in document['text']
            assert 'D1=[formula: =2+2; no cached value]' in document['text']
            assert 'never recalculated' in document['extraction']
        else:
            assert document['text'].index('Synthetic Office evidence') < document['text'].index('Second slide evidence')
            assert 'Speaker evidence' in document['text']


def test_office_and_text_share_aggregate_context_budget(tmp_path):
    client, codex = make(tmp_path)
    with client:
        login(client)
        first = upload(client, b'a' * 99980).json()
        second = upload(client, archive(parts('docx', 'Bounded content ' * 20)), MIMES['docx'], 'long.docx').json()
        connect(client)
        response = send(client, [first['id'], second['id']])
        assert response.status_code == 200, response.text
        docs = documents(codex)
        assert sum(len(item['text']) for item in docs) == 100000
        assert docs[0]['truncated'] is False and docs[1]['truncated'] is True
        assert len(docs[1]['text']) == 20


@pytest.mark.parametrize('kind', ['docx', 'xlsx', 'pptx'])
def test_companion_forwards_exact_office_bytes_through_native_ingress(tmp_path, kind):
    observed = []

    def handler(request):
        if request.url.path.endswith('/session'):
            return httpx.Response(200, json={'session_channel_extension_id': 'web-app'})
        if request.url.path.endswith('/messages'):
            observed.append(json.loads(request.content))
            return httpx.Response(200, json={'accepted': True, 'run_id': 'office-run', 'accepted_message_ref': 'msg:office-message'})
        return httpx.Response(200, json={})

    runtime = IronClaw('http://127.0.0.1:46410', None, token='test', transport=httpx.MockTransport(handler))
    app = create_app(tmp_path, {'http://testserver'}, bootstrap='bootstrap-for-tests', codex=FakeCodex(), runtime=runtime)
    data = archive(parts(kind))
    with TestClient(app) as client:
        login(client)
        item = upload(client, data, MIMES[kind], 'evidence.' + kind).json()
        body = {'text': 'Summarize', 'attachmentIds': [item['id']], 'requestId': str(uuid.uuid4())}
        response = client.post('/api/companion/threads/test/messages', json=body, headers=ORIGIN)
        assert response.status_code == 200, response.text
        assert observed[0]['attachments'] == [{'mime_type': MIMES[kind], 'filename': 'evidence.' + kind, 'data_base64': base64.b64encode(data).decode()}]
        assert client.post('/api/companion/threads/test/messages', json=body, headers=ORIGIN).json() == response.json()
        assert len(observed) == 1


def invalid_archives():
    valid = parts('docx')
    main = 'word/document.xml'
    yield b'not a zip', 'Invalid Office'
    yield archive(parts('xlsx')), 'match'
    yield archive({**valid, main: '<broken>'}), 'Invalid Office'
    yield archive({**valid, main: '<!DOCTYPE document [<!ENTITY x "boom">]><document>&x;</document>'}), 'DTD'
    yield archive({**valid, 'other.xml': '<!DOCTYPE a SYSTEM "file:///etc/passwd"><a/>'}), 'DTD'
    yield archive({**valid, 'other.xml': '<!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>'.encode('utf-16')}), 'DTD'
    yield archive({**valid, '../escape.xml': '<a/>'}), 'unsafe'
    yield archive({**valid, 'word/vbaProject.bin': b'fake macro'}), 'Macro'
    yield archive({**valid, '[Content_Types].xml': valid['[Content_Types].xml'].replace('document.main', 'document.macroEnabled.main')}), 'Macro'
    yield archive({**valid, '[Content_Types].xml': valid['[Content_Types].xml'].replace('</Types>', '<Override PartName="/xl/macrosheets/sheet1.xml" ContentType="application/vnd.ms-excel.macrosheet+xml"/></Types>')}), 'Macro'
    yield archive({**valid, 'EncryptionInfo': b'encrypted'}), 'Encrypted'
    yield b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'fake encrypted container', 'Encrypted'
    yield archive({**valid, 'bomb.xml': '<a>' + 'z' * 1000000 + '</a>'}), 'compression-ratio'
    yield archive({**valid, 'deep.xml': '<a>' * 66 + '</a>' * 66}, zipfile.ZIP_STORED), 'nesting'
    yield archive({**valid, **{f'media/{i}': b'' for i in range(2049)}}), 'entry'
    data = bytearray(archive(valid))
    # Set encryption in both local and central headers, preserving an otherwise valid archive.
    for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        index = data.index(signature) + offset
        flags = struct.unpack_from('<H', data, index)[0]
        struct.pack_into('<H', data, index, flags | 1)
    yield bytes(data), 'Encrypted'
    data = bytearray(archive(valid))
    # A reserved DEFLATE block type raises zlib.error, not necessarily BadZipFile.
    name_len, extra_len = struct.unpack_from('<HH', data, 26)
    data[30 + name_len + extra_len] = 7
    yield bytes(data), 'Invalid Office'


@pytest.mark.parametrize('data,message', list(invalid_archives()))
def test_unsafe_or_malformed_office_rejected_by_upload_before_dispatch(tmp_path, data, message):
    client, codex = make(tmp_path)
    with client:
        login(client)
        response = upload(client, data, MIMES['docx'], 'unsafe.docx')
        assert response.status_code == 422, response.text
        assert message.lower() in response.json()['detail'].lower()
        assert not [method for method, _ in codex.calls if method == 'turn/start']
        with client.app.state.store.connect() as db:
            assert db.execute('SELECT count(*) FROM attachments').fetchone()[0] == 0


@pytest.mark.parametrize('name,mime,message', [
    ('old.doc', 'application/msword', 'save as DOCX'),
    ('old.xls', 'application/vnd.ms-excel', 'save as DOCX'),
    ('old.ppt', 'application/vnd.ms-powerpoint', 'save as DOCX'),
    ('macro.docm', 'application/vnd.ms-word.document.macroEnabled.12', 'Macro-enabled'),
    ('wrong.xlsx', MIMES['docx'], 'must match'),
    ('hidden.docx', 'text/plain', 'must match'),
    ('wrong.txt', MIMES['docx'], 'must match'),
])
def test_office_extension_mime_and_legacy_messages(tmp_path, name, mime, message):
    client, _ = make(tmp_path)
    with client:
        login(client)
        response = upload(client, archive(parts('docx')), mime, name)
        assert response.status_code == 422 and message in response.json()['detail']


@pytest.mark.parametrize('active', [False, True])
async def test_shared_owner_start_and_steer_include_office_context(tmp_path, monkeypatch, active):
    async with api_owner(tmp_path, monkeypatch, active=active) as (client, _, observed, other, _):
        data = archive(parts('docx'))
        upload_response = await client.post('/api/attachments?filename=owner.docx', content=data, headers={'content-type': MIMES['docx']})
        assert upload_response.status_code == 201, upload_response.text
        view = (await client.get(f'/api/codex/threads/{SHARED_THREAD}')).json()
        response = await client.post(f'/api/codex/threads/{SHARED_THREAD}/turns', json={'text': 'Inspect', 'requestId': 'office-owner', 'generation': view['generation'], 'attachmentIds': [upload_response.json()['id']]})
        assert response.status_code == 200, response.text
        method = 'thread-follower-steer-turn' if active else 'thread-follower-start-turn'
        packet = next(item for item in observed if item['method'] == method)['params']
        sent = packet if active else packet['turnStart']['request']
        attached = sent['additionalContext']['leam.attachments']
        assert attached['kind'] == 'untrusted'
        doc = json.loads(attached['value'])['documents'][0]
        assert 'Synthetic Office evidence' in doc['text']
        assert Path(doc['localPath']).read_bytes() == data
        assert 'model' not in sent and 'approvalPolicy' not in sent
        assert not other.calls


@pytest.mark.parametrize('kind', ['docx', 'xlsx', 'pptx'])
def test_office_backup_restore_preserves_original_and_rebuilds_document(tmp_path, kind):
    from test_backups import prepared

    from leam_api.attachments import AttachmentStore
    from leam_api.backups import Backups, restore
    from leam_api.store import Store

    source = tmp_path / 'source'
    store = prepared(source)
    attachments = AttachmentStore(store)
    data = archive(parts(kind))
    item = attachments.add(data, MIMES[kind], 'evidence.' + kind)
    attachments.bind([item['id']], 'office-backup')
    backup = Backups(store).create()
    destination = tmp_path / 'restored'
    restore(Backups(store).path(backup['id']), destination, source)
    restored = AttachmentStore(Store(destination))
    row = restored.rows([item['id']])[0]
    assert row['bytes'] == data
    path = restored.materialize(row)
    assert path.suffix == '.' + kind and path.read_bytes() == data
    with pytest.raises(ValueError, match='retained'):
        restored.remove(item['id'])


def test_empty_word_document_does_not_silently_dispatch_blank_context(tmp_path):
    client, codex = make(tmp_path)
    package = parts('docx')
    package['word/document.xml'] = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>'
    with client:
        login(client)
        response = upload(client, archive(package), MIMES['docx'], 'empty.docx')
        assert response.status_code == 201, response.text
        connect(client)
        response = send(client, [response.json()['id']])
        assert response.status_code == 422 and 'no text' in response.json()['detail']
        assert not [method for method, _ in codex.calls if method == 'turn/start']


def test_updates_ticket_turn_keeps_office_and_ticket_policy_context(tmp_path, monkeypatch):
    from test_ticket_chat import make_ticket

    app, codex, ticket = make_ticket(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        response = upload(client, archive(parts('xlsx')), MIMES['xlsx'], 'ticket.xlsx')
        assert response.status_code == 201, response.text
        item = response.json()
        opened = client.post(f"/api/updates/{ticket['id']}/chat", headers=ORIGIN)
        assert opened.status_code == 200, opened.text
        response = client.post('/api/codex/threads/ticket-thread/turns', json={'text': 'Read these files', 'requestId': 'office-ticket', 'attachmentIds': [item['id']]}, headers=ORIGIN)
        assert response.status_code == 200, response.text
        doc = documents(codex)[0]
        assert 'Synthetic Office evidence' in doc['text']
        sent = next(params for method, params in codex.calls if method == 'turn/start')
        assert sent['additionalContext']['leam.update-ticket']['kind'] == 'application'
        assert 'Ticket title' in sent['additionalContext']['leam.update-ticket']['value']
