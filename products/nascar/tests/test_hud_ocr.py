import asyncio
import os
import threading
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from yourpitbox_nascar.hud_ocr import result_reading
from yourpitbox_nascar.models import OCRConfig
from yourpitbox_nascar.observer import Observer, parse_reading, recognize


@pytest.mark.parametrize('score', [.97, float('nan'), float('inf'), 1.2])
def test_unclear_or_invalid_scores_cannot_become_observations(score):
    result = result_reading(SimpleNamespace(txts=['174'], scores=[score]))
    assert result['accepted'] is False


def test_multiple_text_lines_cannot_be_silently_combined():
    result = result_reading(SimpleNamespace(txts=['17', '50'], scores=[.999, .999]))
    assert result['accepted'] is False


@pytest.mark.skipif(os.name != 'nt', reason='Windows font fixture')
@pytest.mark.parametrize('field,value', [('current_lap',17), ('position',3), ('speed_mph',174)])
async def test_italic_numeric_hud_values(field, value):
    pytest.importorskip('rapidocr')
    font = ImageFont.truetype('C:/Windows/Fonts/arialbi.ttf', 48)
    bounds = font.getbbox(str(value))
    image = Image.new('RGB', (bounds[2]-bounds[0]+14, bounds[3]-bounds[1]+14), 'black')
    ImageDraw.Draw(image).text((7-bounds[0],7-bounds[1]),str(value),font=font,fill='white')
    assert parse_reading(field, await recognize(image)) == value


async def test_slow_ocr_does_not_block_radio_event_loop(monkeypatch):
    import yourpitbox_nascar.observer as module
    started, release = threading.Event(), threading.Event()
    def slow_reader(frames):
        started.set()
        if not release.wait(3):
            raise RuntimeError('test reader timed out')
        return [{'text':'17','score':.999,'accepted':True}]
    monkeypatch.setattr(module, 'read_regions', slow_reader)
    observer = Observer(SimpleNamespace())
    config = OCRConfig(window_id=1, regions=[{'field':'current_lap','x':0,'y':0,'width':1,'height':1}])
    task = asyncio.create_task(observer.read(Image.new('RGB',(100,100)), config))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        # Local proactive speech and HTTP polling share this event loop.
        assert not task.done()
        await asyncio.wait_for(asyncio.sleep(.01), timeout=.25)
    finally:
        release.set()
    assert (await task)['current_lap']['value'] == 17


async def test_observer_cannot_start_with_no_readable_values(monkeypatch):
    import yourpitbox_nascar.observer as module
    monkeypatch.setattr(module, 'capture', lambda _: Image.new('RGB',(100,100)))
    engine = SimpleNamespace(sid='test',config=SimpleNamespace(mode='manual'))
    observer = Observer(engine)
    async def unreadable(*_):
        return {'current_lap':{'text':'','value':None}}
    monkeypatch.setattr(observer, 'read', unreadable)
    config = OCRConfig(window_id=1,regions=[{'field':'current_lap','x':0,'y':0,'width':1,'height':1}])
    with pytest.raises(ValueError,match='No configured HUD values'):
        await observer.start(config)
    assert observer.status['running'] is False
    assert observer.task is None
