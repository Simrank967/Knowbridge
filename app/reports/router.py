from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from uuid import uuid4
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from ..db import DB
from ..deps import get_db
from .service import generate
router=APIRouter(prefix='/reports',tags=['KNOWBRIDGE Reports'])
class ReportRequest(BaseModel): request:str=Field(...,min_length=3,max_length=5000); conversation_id:str|None=None; format:str='pdf'
@router.post('/generate')
def create_report(body:ReportRequest,db:DB=Depends(get_db)):
    if not db.documents(): raise HTTPException(409,'connect a project source before generating a report')
    if body.format.lower()!='pdf': raise HTTPException(422,'PDF is currently the supported downloadable report format')
    cid=body.conversation_id or uuid4().hex
    if not db.get_conversation(cid): db.create_conversation(cid,None,body.request[:64])
    db.save_message(cid,'user',body.request)
    result=generate(db,body.request,cid,body.format)
    db.save_message(cid,'assistant',result['summary'],{'report_id':result['report_id'],'download_url':result['download_url']})
    result['conversation_id']=cid
    return result
@router.get('/{report_id}')
def report_info(report_id:str,db:DB=Depends(get_db)):
    r=db.get_report(report_id)
    if not r: raise HTTPException(404,'report not found')
    r.pop('path',None); return r
@router.get('/{report_id}/download')
def download(report_id:str,db:DB=Depends(get_db)):
    r=db.get_report(report_id)
    if not r or not Path(r['path']).is_file(): raise HTTPException(404,'report file not found')
    return FileResponse(r['path'],media_type='application/pdf',filename=f"knowbridge-{report_id}.pdf")
