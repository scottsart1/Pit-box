"""GPT-6, transcription and opt-in radio migration contracts."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from pitwall.audio import AudioService
from pitwall.config import Settings, settings
from pitwall.providers import OpenAIResponsesProvider
from pitwall.realtime import RealtimeRadio
from pitwall.settings_service import apply_saved_overrides, settings_view
from pitwall.voice import NativeVoiceController


@pytest.mark.asyncio
@pytest.mark.parametrize('route,effort,model', [
    ('normal', 'low', 'gpt-6-luna'), ('deep', 'high', 'gpt-6-sol'),
])
async def test_gpt6_responses_round_trip_keeps_reasoning_and_tool_evidence(route, effort, model):
    requests = []

    def respond(request):
        assert request.url.path == '/v1/responses'
        body = json.loads(request.content)
        requests.append(body)
        output = [
            {'type': 'reasoning', 'id': 'rs_1', 'summary': [], 'encrypted_content': 'opaque-continuation'},
            {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_gap', 'name': 'get_gap', 'arguments': '{}'},
        ] if len(requests) == 1 else [
            {'type': 'message', 'id': 'msg_1', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': 'Norris 1.4 seconds ahead.', 'annotations': []}]},
        ]
        return httpx.Response(200, json={
            'id': 'resp_1', 'object': 'response', 'created_at': 1, 'model': model,
            'status': 'completed', 'output': output,
            'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15},
        })

    async with AsyncOpenAI(api_key='test', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        provider = OpenAIResponsesProvider(Settings(_env_file=None), client=client)
        execute = AsyncMock(return_value={'gap_s': 1.4})
        result = await provider.generate(prompt='Gap ahead?', instructions='Use telemetry.', route=route,
            effort=effort, tools=[{'type': 'function', 'name': 'get_gap', 'description': 'Gap',
            'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
            'strict': True}], execute_tool=execute, max_rounds=3)
    assert result.model == model
    assert result.text == 'Norris 1.4 seconds ahead.'
    assert result.usage['total_tokens'] == 30
    execute.assert_awaited_once_with('get_gap', {})
    assert len(requests) == 2
    for request in requests:
        assert request['model'] == model
        assert request['reasoning'] == {'effort': effort}
        assert request['parallel_tool_calls'] is True
        assert 'temperature' not in request
    continuation = requests[1]['input']
    assert any(item.get('encrypted_content') == 'opaque-continuation' for item in continuation)
    assert any(item.get('call_id') == 'call_gap' and json.loads(item['output']) == {'gap_s': 1.4}
               for item in continuation if item.get('type') == 'function_call_output')


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['gpt-transcribe', 'gpt-4o-mini-transcribe'])
async def test_transcription_wire_format_and_wake_prompt(tmp_path, monkeypatch, model):
    monkeypatch.setattr(settings, 'stt_model', model)
    wav = tmp_path / 'radio.wav'
    wav.write_bytes(b'RIFF-test-fixture')
    requests = []

    def respond(request):
        assert request.url.path == '/v1/audio/transcriptions'
        requests.append(request.content.decode())
        if model == 'gpt-4o-mini-transcribe':
            return httpx.Response(200, text='Mark, box for hards.')
        return httpx.Response(200, json={'text': 'Mark, box for hards.', 'languages': [{'code': 'en'}]})

    audio = AudioService.__new__(AudioService)
    async with AsyncOpenAI(api_key='test', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        audio.client = client
        assert await audio.transcribe(wav, wake_phrases=['mark', 'marc']) == 'Mark, box for hards.'
    body = requests[0]
    assert model in body
    assert 'never adding a name that was not said' in body
    if model == 'gpt-transcribe':
        assert 'name="languages[]"' in body
        assert 'name="language"' not in body
        assert '\r\njson\r\n' in body
    else:
        assert 'name="language"' in body
        assert 'languages' not in body


@pytest.mark.asyncio
async def test_realtime_toggle_is_lazy_closes_active_session_and_can_reenable(stack, monkeypatch, tmp_path):
    store, _, _, _, _, tools = stack
    monkeypatch.setattr(settings, 'voice_realtime_enabled', False)
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    controller = NativeVoiceController(store, SimpleNamespace(tools=tools), SimpleNamespace(stop_playback=lambda: None))
    assert controller.realtime is None
    await controller.configure_realtime(True)
    radio = controller.realtime
    assert isinstance(radio, RealtimeRadio)
    assert radio.is_open is False  # enabling alone must not open a socket
    connection = SimpleNamespace(close=AsyncMock())
    radio._connection = connection
    await controller.configure_realtime(False)
    connection.close.assert_awaited_once()
    assert controller.realtime is None
    assert not await controller._start_realtime(None, 'ptt')
    await controller.configure_realtime(True)
    assert controller.realtime is not radio
    assert not controller.realtime.is_open
    await radio.client.close()
    await controller.realtime.client.close()


@pytest.mark.asyncio
async def test_switching_off_waits_for_inflight_open_then_closes_it(stack, monkeypatch, tmp_path):
    store, _, _, _, _, tools = stack
    monkeypatch.setattr(settings, 'voice_realtime_enabled', False)
    monkeypatch.setattr(settings, 'data_dir', tmp_path)
    controller = NativeVoiceController(store, SimpleNamespace(tools=tools), SimpleNamespace(stop_playback=lambda: None))
    entered, release = asyncio.Event(), asyncio.Event()

    async def open_radio():
        entered.set()
        await release.wait()
        return True

    radio = SimpleNamespace(open=open_radio, close=AsyncMock())
    controller.realtime = radio
    opening = asyncio.create_task(controller._start_realtime(None, 'ptt'))
    await entered.wait()
    disabling = asyncio.create_task(controller.configure_realtime(False))
    await asyncio.sleep(0)
    assert not disabling.done()
    release.set()
    assert await opening
    await disabling
    radio.close.assert_awaited_once()
    assert controller.realtime is None


@pytest.mark.asyncio
async def test_settings_toggle_persists_live_and_rejects_missing_key(stack, monkeypatch):
    import pitwall.app as app_module
    store, database, _, _, _, _ = stack
    controller = SimpleNamespace(configure_realtime=AsyncMock())
    monkeypatch.setattr(app_module, 'database', database)
    monkeypatch.setattr(app_module, 'store', store)
    monkeypatch.setattr(app_module, 'voice', controller)
    monkeypatch.setattr(settings, 'voice_realtime_enabled', False)
    monkeypatch.setattr(settings, 'openai_api_key', None)
    from fastapi import HTTPException
    with pytest.raises(HTTPException, match='OpenAI API key'):
        await app_module.save_app_settings({'voice_realtime_enabled': True})
    assert await database.load_preference('app_settings', None) is None
    from pydantic import SecretStr
    monkeypatch.setattr(settings, 'openai_api_key', SecretStr('test'))
    for enabled in (True, False):
        result = await app_module.save_app_settings({'voice_realtime_enabled': enabled})
        assert result['results']['voice_realtime_enabled'] == 'applied'
        controller.configure_realtime.assert_awaited_with(enabled)
        saved = await database.load_preference('app_settings', None)
        assert saved['voice_realtime_enabled'] is enabled
        restarted = Settings(_env_file=None)
        apply_saved_overrides(restarted, database.path)
        assert restarted.voice_realtime_enabled is enabled
        entry = next(s for s in settings_view(restarted, saved) if s['name'] == 'voice_realtime_enabled')
        assert entry['source'] == 'saved in app'
        assert entry['restart_required'] is False


@pytest.mark.asyncio
async def test_realtime_rejected_session_closes_and_allows_standard_voice(stack):
    store, _, _, _, _, tools = stack
    class RejectedConnection:
        close = AsyncMock()
        send = AsyncMock()
        def __aiter__(self):
            return self.events()
        async def events(self):
            yield SimpleNamespace(type='error', error=SimpleNamespace(message='Model unavailable'))
    connection = RejectedConnection()
    manager = SimpleNamespace(enter=AsyncMock(return_value=connection), __aexit__=AsyncMock())
    radio = RealtimeRadio(store, tools)
    await radio.client.close()
    radio.client = SimpleNamespace(realtime=SimpleNamespace(connect=lambda **_: manager))
    assert not await radio.open()
    assert not radio.is_open
    connection.close.assert_awaited_once()
    manager.__aexit__.assert_awaited_once()
    assert radio._receive_task is None
    assert 'Model unavailable' in (await store.snapshot_live())['last_error']


@pytest.mark.asyncio
async def test_realtime_uses_new_transcription_hint_without_legacy_language(stack, monkeypatch):
    store, _, _, _, _, tools = stack
    monkeypatch.setattr(settings, 'stt_model', 'gpt-transcribe')
    radio = RealtimeRadio(store, tools)
    assert (await radio._session_payload())['audio']['input']['transcription'] == {
        'model': 'gpt-transcribe', 'languages': ['en'],
    }
    await radio.client.close()


@pytest.mark.asyncio
async def test_cancelling_connection_handshake_releases_socket(stack):
    store, _, _, _, _, tools = stack
    waiting = asyncio.Event()
    class WaitingConnection:
        close = AsyncMock()
        send = AsyncMock()
        def __aiter__(self):
            return self.events()
        async def events(self):
            waiting.set()
            await asyncio.Event().wait()
            yield SimpleNamespace(type='session.updated')
    connection = WaitingConnection()
    manager = SimpleNamespace(enter=AsyncMock(return_value=connection), __aexit__=AsyncMock())
    radio = RealtimeRadio(store, tools)
    await radio.client.close()
    radio.client = SimpleNamespace(realtime=SimpleNamespace(connect=lambda **_: manager))
    opening = asyncio.create_task(radio.open())
    await waiting.wait()
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert not radio.is_open
    connection.close.assert_awaited_once()
    manager.__aexit__.assert_awaited_once()
