import os, uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client

app = FastAPI()
db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

class Item(BaseModel):
    product_id: int
    qty: int

class OrderIn(BaseModel):
    customer_name: str
    hub_id: int
    backup_hub_id: int
    items: list[Item]

@app.get("/")
def home():
    return {"message": "Parcel backend chal raha hai"}

@app.get("/hubs")
def hubs():
    return db.table("hubs").select("*").execute().data

@app.get("/products")
def products():
    return db.table("products").select("*").execute().data

@app.post("/orders")
def create_order(order: OrderIn):
    total = 0
    for it in order.items:
        rows = db.table("products").select("*").eq("id", it.product_id).execute().data
        if not rows:
            raise HTTPException(404, "Product nahi mila")
        p = rows[0]
        if it.qty < 1 or it.qty > p["stock"]:
            raise HTTPException(400, "Stock kam hai: " + p["name"])
        total += p["price"] * it.qty
    delivery_id = "DLV-" + uuid.uuid4().hex[:8].upper()
    db.table("orders").insert({
        "delivery_id": delivery_id,
        "customer_name": order.customer_name,
        "hub_id": order.hub_id,
        "backup_hub_id": order.backup_hub_id,
        "total": total,
    }).execute()
    for it in order.items:
        p = db.table("products").select("stock").eq("id", it.product_id).execute().data[0]
        db.table("products").update({"stock": p["stock"] - it.qty}).eq("id", it.product_id).execute()
    return {"delivery_id": delivery_id, "total": total}

@app.get("/orders/{delivery_id}")
def get_order(delivery_id: str):
    rows = db.table("orders").select("*").eq("delivery_id", delivery_id).execute().data
    if not rows:
        raise HTTPException(404, "Order nahi mila")
    return rows[0]
