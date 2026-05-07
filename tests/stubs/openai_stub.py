"""
tests/stubs/openai_stub.py -- OpenAI Completions API Stub Server
A FastAPI server mimicking POST /v1/chat/completions with scripted responses.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path
from typing import Any
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_FIXTURES_PATH = Path(__file__).parent.parent / 'fixtures' / 'openai_responses.json'

def _load_fixtures():
    if _FIXTURES_PATH.exists():
        with open(_FIXTURES_PATH, encoding='utf-8') as f:
            return json.load(f)
    return {}

_state = {
    'active_scenario': None,
    'call_log': [],
    'fixtures': _load_fixtures(),
}
DEFAULT_CONTENT = '[]'

def _make_completion_response(content, model='gpt-4.1-mini'):
    return {
        'id': f'stub-{int(time.time() * 1000)}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model,
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 10, 'completion_tokens': len(content.split()), 'total_tokens': 10 + len(content.split())},
    }

app = FastAPI(title='Navi OpenAI Stub Server', version='1.0.0')

@app.post('/v1/chat/completions')
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get('model', 'gpt-4.1-mini')
    messages = body.get('messages', [])
    scenario = _state['active_scenario']
    content = _state['fixtures'].get(scenario, DEFAULT_CONTENT) if scenario else DEFAULT_CONTENT
    _state['call_log'].append({'timestamp': time.time(), 'model': model, 'messages': messages, 'scenario_used': scenario, 'response_content': content})
    _state['active_scenario'] = None
    return JSONResponse(content=_make_completion_response(content, model))

@app.post('/stub/set-response')
async def set_response(request: Request):
    body = await request.json()
    scenario = body.get('scenario')
    if scenario and scenario not in _state['fixtures']:
        return JSONResponse(status_code=400, content={'error': f'Unknown scenario: {scenario}'})
    _state['active_scenario'] = scenario
    return JSONResponse({'ok': True, 'active_scenario': scenario})

@app.get('/stub/calls')
async def get_calls():
    return JSONResponse({'total_calls': len(_state['call_log']), 'calls': _state['call_log']})

@app.post('/stub/reset')
async def reset():
    _state['active_scenario'] = None
    _state['call_log'] = []
    _state['fixtures'] = _load_fixtures()
    return JSONResponse({'ok': True, 'message': 'Stub state reset.'})

@app.get('/stub/health')
async def health():
    return JSONResponse({'status': 'ok', 'active_scenario': _state['active_scenario'], 'total_calls': len(_state['call_log']), 'fixtures_loaded': len(_state['fixtures'])})

if __name__ == '__main__':
    port = int(os.getenv('PORT', '11435'))
    print(f'OpenAI stub server starting on port {port}')
    uvicorn.run(app, host='0.0.0.0', port=port, log_level='info')
