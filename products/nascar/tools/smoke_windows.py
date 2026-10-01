"""Exercise the actual frozen executable, persistence, and Windows OCR DLLs."""
from __future__ import annotations

import argparse
import ctypes
import json
import re
import socket
import subprocess
import tempfile
import multiprocessing
import time
from pathlib import Path

import httpx


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def hud_fixture(ready, stop, handle):
    import tkinter as tk
    root=tk.Tk();root.title('YourPitBox NASCAR synthetic OCR validation')
    root.geometry('500x160+40+40');root.configure(bg='black')
    label=tk.Label(root,text='FUEL 71.5%',font=('Arial',40),fg='white',bg='black');label.pack(expand=True,fill='both')
    root.update();root.lower()
    user=ctypes.WinDLL('user32');user.GetAncestor.argtypes=[ctypes.c_void_p,ctypes.c_uint];user.GetAncestor.restype=ctypes.c_void_p
    handle.value=int(user.GetAncestor(root.winfo_id(),2));ready.set()
    def check():
        if stop.is_set(): root.destroy()
        else: root.after(100,check)
    root.after(100,check);root.mainloop()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('exe',type=Path);args=parser.parse_args()
    result={}
    with tempfile.TemporaryDirectory(prefix='nascar-frozen-check-') as folder:
        web_port=port();base=f'http://127.0.0.1:{web_port}'
        client=httpx.Client(timeout=15)
        process=None
        def start():
            nonlocal process
            info=subprocess.STARTUPINFO();info.dwFlags|=subprocess.STARTF_USESHOWWINDOW;info.wShowWindow=0
            process=subprocess.Popen([str(args.exe.resolve()),'--no-browser','--no-lan','--port',str(web_port),'--data-dir',folder],startupinfo=info)
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if process.poll() is not None: raise RuntimeError('Frozen executable exited before health was ready')
                try:
                    if client.get(base+'/health').json().get('product')=='yourpitbox-nascar': break
                except (httpx.HTTPError,ValueError): pass
                time.sleep(.2)
            else: raise RuntimeError('Frozen server did not become ready')
            html=client.get(base+'/').text
            token=re.search(r'name="pitbox-token" content="([^"]+)"',html)[1]
            client.headers['x-pitbox-key']=token
        try:
            start();result['frozen_start']=True
            for asset in ('app.js','app.css','icon.svg'):
                assert client.get(base+'/static/'+asset).status_code==200
            sid=client.post(base+'/api/sessions',json={'name':'Frozen smoke','fuel_burn_green':2}).json()['id']
            response=client.post(base+'/api/observations',json={'session_id':sid,'sequence':1,'sample':{'completed_laps':90,'fuel_pct':30,'flag':'green','pit_open':True}})
            assert response.json()['accepted']
            assert client.get(base+'/api/state').json()['fuel']['margin_pct']==4
            result['calculation']=True
            context=multiprocessing.get_context('spawn')
            ready,stop,handle=context.Event(),context.Event(),context.Value('Q',0)
            fixture=context.Process(target=hud_fixture,args=(ready,stop,handle),daemon=True);fixture.start()
            try:
                assert ready.wait(10)
                config={'window_id':handle.value,'regions':[{'field':'fuel_pct','x':.03,'y':.2,'width':.92,'height':.75}]}
                response=client.post(base+'/api/observer/preview',json=config)
                if response.status_code!=200: raise RuntimeError('Frozen OCR preview failed: '+response.text[:300])
                value=response.json()['fuel_pct']['value']
                assert value==71.5,response.json()
                result['frozen_capture_and_ocr']=True
            finally:
                stop.set();fixture.join(5)
                if fixture.is_alive():fixture.terminate();fixture.join(5)
            client.post(base+'/api/quit',json={}).raise_for_status();process.wait(10)
            start()
            snap=client.get(base+'/api/state').json()
            assert snap['session_id']==sid and snap['values']['fuel_pct'] is None and snap['last_observed']['fuel_pct']==30
            result['restart_preserves_history_without_false_live_data']=True
        finally:
            if process and process.poll() is None:
                try: client.post(base+'/api/quit',json={});process.wait(10)
                except (httpx.HTTPError,subprocess.TimeoutExpired): process.terminate();process.wait(5)
            client.close()
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
