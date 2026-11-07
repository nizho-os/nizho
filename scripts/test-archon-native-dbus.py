#!/usr/bin/env python3
"""Run on a private bus against an actual signed disposable Flatpak repository.
Arguments: debug-service-binary isolated-installation private-state-directory.
"""
from pathlib import Path
import subprocess
import sys
import time
import statistics
from gi.repository import Gio,GLib

binary,installation,state=sys.argv[1:]
bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
def launch():
    process=subprocess.Popen([binary,'--session','--test-installation',installation,'--state-dir',state])
    for _ in range(200):
        try:
            call('GetCapabilities');return process
        except GLib.Error:
            if process.poll() is not None:raise RuntimeError('Service startup failed')
            time.sleep(.025)
    raise RuntimeError('Service startup timed out')
def call(method,signature=None,args=()):
    result=bus.call_sync('org.nizho.Archon1','/org/nizho/Archon1','org.nizho.Archon1.Manager',method,GLib.Variant(signature,args) if signature else None,None,Gio.DBusCallFlags.NONE,120000,None)
    return result.unpack()[0] if result.n_children() else None
def rejected(method,signature,args):
    try:call(method,signature,args)
    except GLib.Error:return
    raise AssertionError(method+' unexpectedly succeeded')

started=time.perf_counter()
service=launch()
print(f"Service startup: {(time.perf_counter()-started)*1000:.2f} ms",flush=True)
try:
    before=time.perf_counter()
    rows=call('SearchPackages','(ssuu)',('ArchonTest','',0,50))
    print(f'Catalogue/search: {(time.perf_counter()-before)*1000:.2f} ms',flush=True)
    package=next(row for row in rows if row[0]=='app/org.nizho.ArchonTest/x86_64/stable')
    assert call('GetInstalledPackages')==[]
    assert 'ArchonTestPlatform' in call('GetPackageDetails','(ss)',(package[0],package[4]))
    before=time.perf_counter()
    plan=call('PlanTransaction','(sss)',('install',package[0],package[4]))
    assert 'ArchonTestPlatform' in plan[3]
    print(f'Transaction planning: {(time.perf_counter()-before)*1000:.2f} ms',flush=True)
    samples=[]
    for _ in range(30):
        before=time.perf_counter();call('GetCapabilities');samples.append((time.perf_counter()-before)*1000)
    print(f'D-Bus GetCapabilities median: {statistics.median(samples):.2f} ms',flush=True)
    print(next(line for line in Path(f'/proc/{service.pid}/status').read_text().splitlines() if line.startswith('VmRSS:')),flush=True)
    rejected('CommitTransaction','(s)',(plan[0],)) # debug service cannot authorize system commits
    call('CancelTransaction','(s)',(plan[0],))
    assert call('GetTransactionStatus','(s)',(plan[0],))[1]=='cancelled'
    rejected('CommitTransaction','(s)',(plan[0],))
    pending=call('PlanTransaction','(sss)',('install',package[0],package[4]))
    service.terminate();service.wait(timeout=10)
    service=launch()
    assert call('GetTransactionStatus','(s)',(pending[0],))[1]=='interrupted'
    rejected('CommitTransaction','(s)',(pending[0],))
    assert len(call('GetTransactionHistory','(uu)',(0,100)))==2
    assert call('GetInstalledPackages')==[]
    print('PASS actual service + signed native catalogue/details/dependencies, denied commit, cancellation, restart/history, replay rejection')
    if __import__('os').environ.get('NIZHO_UI_TEST_BINARY'):
        env=dict(__import__('os').environ,NIZHO_ARCHON_TEST_SESSION='1',NIZHO_UI_SERVICE_PID=str(service.pid))
        subprocess.run([env['NIZHO_UI_TEST_BINARY'],'--ignored','--test-threads=1','--nocapture'],env=env,check=True)
        service.wait(timeout=10)
        service=launch()
        print('PASS real GTK search/category/details/confirmation/empty/history/service-loss contract')
finally:
    service.terminate();service.wait(timeout=10)
