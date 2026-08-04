from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import Quotation
from .schemas import (
    QuotationCreate,
    QuotationUpdate,
    QuotationResponse,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

@app.get("/")
def read_root():
    return {"message": "Welcome to the Quotation Service!"}


@app.get("/quotations", response_model=list[QuotationResponse])
def get_all_quotations(db: Session = Depends(get_db)):
    return db.query(Quotation).all()


@app.get("/quotations/{quotation_id}", response_model=QuotationResponse)
def get_quotation(quotation_id: int, db: Session = Depends(get_db)):
    quotation = (
        db.query(Quotation)
        .filter(Quotation.id == quotation_id)
        .first()
    )

    if quotation is None:
        raise HTTPException(status_code=404, detail="Quotation not found")

    return quotation


@app.post("/quotations", response_model=QuotationResponse, status_code=201)
def create_quotation(
    quotation: QuotationCreate,
    db: Session = Depends(get_db),
):
    new_quotation = Quotation(**quotation.model_dump())

    db.add(new_quotation)
    db.commit()
    db.refresh(new_quotation)

    return new_quotation


@app.put("/quotations/{quotation_id}", response_model=QuotationResponse)
def update_quotation(
    quotation_id: int,
    quotation: QuotationUpdate,
    db: Session = Depends(get_db),
):
    db_quotation = (
        db.query(Quotation)
        .filter(Quotation.id == quotation_id)
        .first()
    )

    if db_quotation is None:
        raise HTTPException(status_code=404, detail="Quotation not found")

    for key, value in quotation.model_dump().items():
        setattr(db_quotation, key, value)

    db.commit()
    db.refresh(db_quotation)

    return db_quotation


@app.delete("/quotations/{quotation_id}")
def delete_quotation(
    quotation_id: int,
    db: Session = Depends(get_db),
):
    quotation = (
        db.query(Quotation)
        .filter(Quotation.id == quotation_id)
        .first()
    )

    if quotation is None:
        raise HTTPException(status_code=404, detail="Quotation not found")

    db.delete(quotation)
    db.commit()

    return {"message": "Quotation deleted successfully"}