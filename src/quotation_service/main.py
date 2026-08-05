from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import Quotation
from .schemas import QuotationCreate, QuotationUpdate, QuotationResponse

app = FastAPI(title="Quotation Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {"message": "Quotation Service API", "version": "1.0.0"}


@app.get("/quotations", response_model=list[QuotationResponse])
def get_all_quotations(db: Session = Depends(get_db)):
    quotations = db.query(Quotation).order_by(Quotation.created_at.desc()).all()
    return quotations


@app.get("/quotations/{quotation_id}", response_model=QuotationResponse)
def get_quotation(quotation_id: int, db: Session = Depends(get_db)):
    quotation = db.query(Quotation).filter(Quotation.id == quotation_id).first()
    if not quotation:
        raise HTTPException(status_code=404, detail="Quotation not found")
    return quotation


@app.post("/quotations", response_model=QuotationResponse, status_code=201)
def create_quotation(payload: QuotationCreate, db: Session = Depends(get_db)):
    line_items_data = [item.model_dump() for item in payload.line_items]
    
    new_quotation = Quotation(
        client_name=payload.client_name,
        quotation_title=payload.quotation_title,
        line_items=line_items_data,
        status=payload.status,
    )
    
    db.add(new_quotation)
    db.commit()
    db.refresh(new_quotation)
    
    return new_quotation


@app.put("/quotations/{quotation_id}", response_model=QuotationResponse)
def update_quotation(
    quotation_id: int,
    payload: QuotationUpdate,
    db: Session = Depends(get_db),
):
    quotation = db.query(Quotation).filter(Quotation.id == quotation_id).first()
    if not quotation:
        raise HTTPException(status_code=404, detail="Quotation not found")
    
    update_data = payload.model_dump(exclude_unset=True)
    
    if "line_items" in update_data and update_data["line_items"]:
        update_data["line_items"] = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in update_data["line_items"]
        ]
    
    for key, value in update_data.items():
        setattr(quotation, key, value)
    
    db.commit()
    db.refresh(quotation)
    
    return quotation


@app.delete("/quotations/{quotation_id}")
def delete_quotation(quotation_id: int, db: Session = Depends(get_db)):
    quotation = db.query(Quotation).filter(Quotation.id == quotation_id).first()
    if not quotation:
        raise HTTPException(status_code=404, detail="Quotation not found")
    
    db.delete(quotation)
    db.commit()
    
    return {"message": "Quotation deleted successfully"}
