"""Router mounted by Hermes inside its authenticated plugin namespace only."""
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handle(request):
            try:
                response = await original(request)
            except RequestValidationError:
                return JSONResponse({'detail': 'Invalid request parameters.'}, status_code=422,
                                    headers={'Cache-Control': 'no-store'})
            except HTTPException as exc:
                exc.headers = dict(exc.headers or {}, **{'Cache-Control': 'no-store'})
                raise
            response.headers['Cache-Control'] = 'no-store'
            return response
        return handle

# Direct-path loading supports the installed kebab-case plugin directory.
_name = '_local_agent_security_service'
if _name not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_name, Path(__file__).resolve().parents[1] / 'service.py')
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)
service = sys.modules[_name].service
router = APIRouter(route_class=PrivateRoute)


def call(method, **kwargs):
    try:
        return getattr(service, method)(**kwargs)
    except Exception as exc:
        if type(exc).__name__ == 'SecurityError':
            raise HTTPException(status_code=exc.status, detail=exc.detail) from None
        raise HTTPException(status_code=503, detail='Local security service unavailable.') from None


async def body(request, keys):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, 'Invalid request body.') from None
    if not isinstance(data, dict) or set(data) != set(keys):
        raise HTTPException(400, 'Invalid request fields.')
    return data


@router.post('/inspect')
async def inspect(request: Request):
    data = await body(request, ('id', 'include_content'))
    result = call('inspect', event_id=data['id'], include_content=data['include_content'])
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.post('/label')
async def label(request: Request):
    data = await body(request, ('id', 'value'))
    return call('label', event_id=data['id'], value=data['value'])


@router.get('/replay')
def replay_status():
    return call('replay_status')


@router.post('/replay', status_code=202)
async def replay(request: Request):
    data = await body(request, ('model',))
    return call('start_replay', model=data['model'])


@router.get('/status')
def status(session_id: str | None = None):
    return call('status', session_id=session_id)


@router.get('/events')
def events(session_id: str | None = None, all_sessions: bool = False,
           include_probes: bool = False, limit: int = Query(50, ge=1, le=500)):
    return call('events', session_id=session_id, all_sessions=all_sessions, include_probes=include_probes, limit=limit)


@router.get('/events/{event_id}')
def event(event_id: str):
    return call('event', event_id=event_id)
