import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen, Request

import pytest
from http.server import ThreadingHTTPServer

from dashboard.__main__ import BacktestApp, handler_for
from dashboard.live_view import live_snapshot
from dashboard.strategy_notes import strategy_definition
from strategies import get_strategies


def make_journal(path, real=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE orders(id TEXT,side TEXT,quantity TEXT,price TEXT,status TEXT); CREATE TABLE fills(order_id TEXT,quantity TEXT,cost TEXT,fee TEXT);')
        db.execute('INSERT INTO meta VALUES (?,?)', ('identity', json.dumps(['moving_average','version','source',real,'private-account-fingerprint',{}])))
        db.execute('INSERT INTO orders VALUES (?,?,?,?,?)', ('private-client-1','buy','.001','50000','partially_filled'))
        db.execute('INSERT INTO orders VALUES (?,?,?,?,?)', ('private-client-2','sell','.0005','51000','filled'))
        db.execute('INSERT INTO fills VALUES (?,?,?,?)', ('private-client-1','.0005','25','.1'))
        db.execute('INSERT INTO fills VALUES (?,?,?,?)', ('private-client-2','.0005','25.5','.1'))


def test_live_projection_real_only_and_not_runtime_status(tmp_path):
    make_journal(tmp_path/'real.sqlite')
    make_journal(tmp_path/'demo.sqlite', real=False)
    before = (tmp_path/'real.sqlite').read_bytes()
    result = live_snapshot(tmp_path)
    assert result['complete'] and not result['execution_status_observable']
    assert len(result['journals']) == 1
    row = result['journals'][0]
    assert row['execution_status'] is None and row['strategy'] == 'moving_average'
    assert [o['status'] for o in row['orders']] == ['partially_filled','filled']
    assert row['fill_count'] == 2
    assert row['orders'][0]['fills'] == [{'quantity':'.0005','cost':'25','fee':'.1'}]
    assert row['orders'][1]['fills'] == [{'quantity':'.0005','cost':'25.5','fee':'.1'}]
    assert 'private-client' not in json.dumps(result) and 'private-account' not in json.dumps(result)
    assert (tmp_path/'real.sqlite').read_bytes() == before


def test_no_journals_creates_no_directory_or_strategy(tmp_path):
    missing = tmp_path/'missing'
    assert live_snapshot(missing)['journals'] == []
    assert not missing.exists()


def test_corrupt_journal_is_partial_not_empty_live_proof(tmp_path):
    (tmp_path/'broken.sqlite').write_bytes(b'not sqlite')
    result = live_snapshot(tmp_path)
    assert not result['complete'] and len(result['errors']) == 1
    assert result['journals'] == [] and not result['execution_status_observable']


def test_unknown_identity_not_assumed_real(tmp_path):
    make_journal(tmp_path/'unknown.db')
    with sqlite3.connect(tmp_path/'unknown.db') as db:
        db.execute('UPDATE meta SET value=?', (json.dumps(['old-identity']),))
    result=live_snapshot(tmp_path)
    assert not result['complete'] and not result['journals']


def test_live_reader_can_read_wal_without_writing(tmp_path):
    path=tmp_path/'real.sqlite'
    make_journal(path)
    writer=sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    try:
        writer.execute('UPDATE orders SET status="open" WHERE side="buy"')
        writer.commit()
        writer.execute('BEGIN IMMEDIATE')
        writer.execute('UPDATE orders SET status="unknown" WHERE side="buy"')
        result=live_snapshot(tmp_path)
        assert result['journals'][0]['orders'][0]['status'] == 'open'
        writer.rollback()
    finally:
        writer.close()


def test_all_strategy_notes_match_current_code_and_go_stale_explicitly():
    for key,spec in get_strategies().items():
        definition=strategy_definition(key,spec)
        assert definition['verified'], key
        assert definition['notes']['formulas'] and definition['notes']['entry'] and definition['notes']['exit']
        old=strategy_definition(key,{**spec,'source_hash':'modified'})
        assert not old['verified'] and old['notes'] is None
    assert strategy_definition('missing',get_strategies()['rsi'])['notes'] is None


def test_metadata_does_not_change_functions_or_defaults(tmp_path):
    specs=get_strategies()
    function=specs['moving_average']['function']
    defaults=function.__defaults__
    app=BacktestApp(tmp_path/'results.json',paper_dir=tmp_path/'paper',live_dir=tmp_path/'live')
    snapshot=app.snapshot()
    assert snapshot['strategies']['moving_average']['definition']['defaults'] == {'short_window':10,'long_window':30}
    assert function.__defaults__ == defaults
    assert app.paper.snapshot() == {}
    assert not (tmp_path/'live').exists()
    assert not (tmp_path/'results.json').exists()


@pytest.fixture
def server(tmp_path):
    app=BacktestApp(tmp_path/'results.json',paper_dir=tmp_path/'paper',live_dir=tmp_path/'live')
    make_journal(tmp_path/'live'/'real.sqlite')
    http=ThreadingHTTPServer(('127.0.0.1',0),handler_for(app))
    thread=Thread(target=http.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{http.server_port}'
    http.shutdown(); http.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize('route', ['/','/paper','/live','/strategy?name=rsi','/workspace.js','/live.js'])
def test_workspace_routes_available(server,route):
    with urlopen(server+route) as response:
        assert response.status == 200
        text=response.read().decode()
        if route in ('/','/paper','/live','/strategy?name=rsi'):
            for label in ('Strategy Lab','Paper Trading','Live Trading'):
                assert label in text


def test_api_live_has_no_write_route(server):
    result=json.load(urlopen(server+'/api/live'))
    assert result['journals'][0]['execution_status'] is None
    with pytest.raises(HTTPError) as error:
        urlopen(Request(server+'/api/live/start',data=b'{}',headers={'Content-Type':'application/json'}))
    assert error.value.code == 404


def test_js_filters_search_and_actual_membership():
    node=shutil.which('node')
    assert node, 'Node est nécessaire pour les tests JS sans dépendance.'
    root=Path(__file__).resolve().parents[1]/'dashboard'
    script=(root/'common.js').read_text()+(root/'workspace.js').read_text()+'''
const assert=require('node:assert/strict');
const row={name:'Moyennes mobiles',status:'done',result:{summary:{pnl:1}}};
const paper={moving_average:{status:'paused'}};
const live={journals:[{strategy:'rsi'}]};
assert(strategyMatches('moving_average',row,'paper','MÔBILES',paper,live));
assert(!strategyMatches('moving_average',row,'live','',paper,live));
assert(strategyMatches('rsi',{name:'RSI'},'live','rsi',paper,live));
assert(!strategyMatches('moving_average',row,'untested','',paper,live));
assert(strategyMatches('moving_average',{...row,eligibility:{status:'not_tested'}},'untested','',paper,live));
assert(!strategyMatches('rsi',{name:'RSI'},'paper','',{},live));
assert(!strategyMatches('__proto__',row,'paper','',{},live));
assert.equal(paperStatus({status:'paused'}),'En pause');
assert.equal(paperStatus({status:'unknown'}),'État non reconnu');
assert(environmentBadges('rsi',{},live).includes('journal enregistré'));
assert(!environmentBadges('rsi',{},live).includes('En cours'));
assert(strategyOverview({definition:{description:'<script>',notes:null,constants:{},defaults:{}}}).includes('&lt;script&gt;'));
'''
    subprocess.run([node,'-e',script],check=True,capture_output=True,text=True)
