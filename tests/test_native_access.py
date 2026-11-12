import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from pitwall.web_security import NativeAccessMiddleware

TOKEN = 'a' * 64


async def endpoint(request):
    return JSONResponse({'ok': True})


async def socket(ws):
    await ws.accept()
    await ws.send_text('ok')
    await ws.close()


def client(token=TOKEN):
    app = Starlette(routes=[Route('/api/health', endpoint), Route('/api/credentials', endpoint, methods=['POST']), WebSocketRoute('/ws', socket)])
    app.add_middleware(NativeAccessMiddleware, token=token)
    return TestClient(app)


def test_loopback_is_not_authentication_and_health_is_protected():
    c = client()
    assert c.get('/api/health').status_code == 401
    assert c.get('/api/health?access_token=' + TOKEN).status_code == 401
    assert c.get('/api/health', headers={'x-pitbox-native': TOKEN}).status_code == 200
    assert c.get('/api/health', headers={'cookie': 'pitbox_native=' + TOKEN}).status_code == 200


def test_valid_cookie_cannot_be_used_from_a_foreign_origin():
    c = client()
    headers = {'cookie': 'pitbox_native=' + TOKEN, 'origin': 'https://evil.example'}
    assert c.post('/api/credentials', headers=headers).status_code == 403
    headers['origin'] = 'http://testserver'
    assert c.post('/api/credentials', headers=headers).status_code == 200


def test_websockets_are_also_protected():
    c = client()
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect('/ws'):
            pass
    with c.websocket_connect('/ws', headers={'x-pitbox-native': TOKEN}) as ws:
        assert ws.receive_text() == 'ok'


def test_desktop_no_native_token_keeps_existing_routes():
    assert client(None).get('/api/health').status_code == 200
