from fastapi import APIRouter, Header, HTTPException
from .services import tv_watching

router = APIRouter()


@router.post('/api/v1/control/tv-watching')
def owner_tv_watching(body: dict, x_homeserver_ui: str | None = Header(default=None)):
    # The installed owner gateway protects control routes. Explicit UI marker
    # additionally prevents ordinary cross-site forms from enabling capture.
    if not isinstance(body.get('action', 'history'), str):
        raise HTTPException(422, detail='TV action must be text.')
    if body.get('action', 'history') not in tv_watching.READ_ACTIONS and x_homeserver_ui != '1':
        raise HTTPException(403, detail='Use the owner TV Watching controls.')
    try:
        return tv_watching.request(body)
    except tv_watching.TVError as exc:
        raise HTTPException(exc.status_code, detail=str(exc)) from exc
