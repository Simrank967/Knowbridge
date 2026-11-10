from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from uuid import uuid4
from ..db import DB
from ..deps import get_db
from .service import answer
router=APIRouter(tags=['KNOWBRIDGE Chat'])
class ChatRequest(BaseModel): message:str=Field(...,min_length=1,max_length=12000); conversation_id:str|None=None; project_id:str|None=None
@router.post('/chat')
def chat(body:ChatRequest,db:DB=Depends(get_db)):
    cid=body.conversation_id or uuid4().hex
    existing=db.get_conversation(cid)
    if not existing: db.create_conversation(cid,body.project_id,body.message[:64])
    db.save_message(cid,'user',body.message)
    result=answer(db,body.message,body.project_id)
    db.save_message(cid,'assistant',result['answer'],{'evidence':result.get('evidence',[]),'verification':result.get('verification')})
    return {'conversation_id':cid,**result}
@router.get('/conversations')
def conversations(db:DB=Depends(get_db)): return {'conversations':db.list_conversations()}
@router.get('/conversations/{conversation_id}')
def conversation(conversation_id:str,db:DB=Depends(get_db)):
    item=db.get_conversation(conversation_id)
    if not item: raise HTTPException(404,'conversation not found')
    return item
@router.delete('/conversations/{conversation_id}')
def delete_conversation(conversation_id:str,db:DB=Depends(get_db)):
    db.delete_conversation(conversation_id); return {'deleted':True}
