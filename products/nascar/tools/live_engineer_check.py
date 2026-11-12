"""Opt-in smoke: supply NASCAR_OPENAI_API_KEY or --key-env-file. Never writes keys."""
import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from yourpitbox_nascar.demo import start_demo
from yourpitbox_nascar.engine import RaceEngine
from yourpitbox_nascar.engineer import Engineer
from yourpitbox_nascar.store import Store


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--key-env-file',type=Path)
    args=parser.parse_args()
    key=os.environ.get('NASCAR_OPENAI_API_KEY','')
    if not key and args.key_env_file:
        for line in args.key_env_file.read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('OPENAI_API_KEY='):
                key=line.split('=',1)[1].strip().strip('"').strip("'")
    if not key: raise SystemExit('No test key configured')
    class MemoryKey:
        def read(self): return key
    with tempfile.TemporaryDirectory(prefix='nascar-engineer-check-') as folder:
        store=Store(Path(folder));engine=RaceEngine(store);start_demo(engine)
        engineer=Engineer(engine,MemoryKey())
        try:
            for question in ['Will the rest of the field need another pit stop?', 'What if we get four caution laps and save five percent of our green-flag fuel?', 'The car is tight in the center late in a run. Give me a radical review, not just a tiny tweak.']:
                result=await engineer.ask(question)
                print(json.dumps({'question':question,**result},ensure_ascii=False),flush=True)
        finally:
            await engineer.close();store.close()


if __name__=='__main__': asyncio.run(main())
