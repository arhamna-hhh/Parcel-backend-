import os, uuid, secrets
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client

app = FastAPI()
db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

RANK = {"S": 1, "M": 2, "L": 3}


class Item(BaseModel):
    product_id: int
    qty: int


class OrderIn(BaseModel):
    customer_name: str
    hub_id: int
    backup_hub_id: int
    items: list[Item]
    payment_option: str = "pay_now"


class CollectIn(BaseModel):
    pickup_code: str


def get_order_row(delivery_id):
    rows = db.table("orders").select("*").eq("delivery_id", delivery_id).execute().data
    if not rows:
        raise HTTPException(404, "Order nahi mila")
    return rows[0]


def new_code():
    return str(secrets.randbelow(900000) + 100000)


def find_locker(hub_id, size):
    lockers = db.table("lockers").select("*").eq("hub_id", hub_id).eq("status", "empty").execute().data
    fits = [l for l in lockers if RANK[l["size"]] >= RANK[size]]
    if not fits:
        return None
    return sorted(fits, key=lambda l: RANK[l["size"]])[0]


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
    if order.payment_option not in ("pay_now", "pay_later"):
        raise HTTPException(400, "payment_option galat hai")
    total = 0
    parcel_size = "S"
    for it in order.items:
        rows = db.table("products").select("*").eq("id", it.product_id).execute().data
        if not rows:
            raise HTTPException(404, "Product nahi mila")
        p = rows[0]
        if it.qty < 1 or it.qty > p["stock"]:
            raise HTTPException(400, "Stock kam hai: " + p["name"])
        total += p["price"] * it.qty
        size = p["size"] or "S"
        if RANK[size] > RANK[parcel_size]:
            parcel_size = size
    delivery_id = "DLV-" + uuid.uuid4().hex[:8].upper()
    db.table("orders").insert({
        "delivery_id": delivery_id,
        "customer_name": order.customer_name,
        "hub_id": order.hub_id,
        "backup_hub_id": order.backup_hub_id,
        "total": total,
        "parcel_size": parcel_size,
        "payment_option": order.payment_option,
    }).execute()
    for it in order.items:
        p = db.table("products").select("stock").eq("id", it.product_id).execute().data[0]
        db.table("products").update({"stock": p["stock"] - it.qty}).eq("id", it.product_id).execute()
    return {"delivery_id": delivery_id, "total": total, "parcel_size": parcel_size}


@app.get("/orders/{delivery_id}")
def get_order(delivery_id: str):
    o = get_order_row(delivery_id)
    o.pop("pickup_code", None)
    return o


@app.post("/orders/{delivery_id}/deposit")
def deposit(delivery_id: str):
    o = get_order_row(delivery_id)
    if o["order_status"] != "placed":
        raise HTTPException(400, "Ye order pehle hi deposit ho chuka hai")
    hubs_to_try = [h for h in (o["hub_id"], o["backup_hub_id"]) if h]
    locker, chosen_hub = None, None
    for h in hubs_to_try:
        locker = find_locker(h, o["parcel_size"])
        if locker:
            chosen_hub = h
            break
    if not locker:
        raise HTTPException(409, "Dono hubs mein khali locker nahi hai")
    claimed = db.table("lockers").update({"status": "occupied"}).eq("id", locker["id"]).eq("status", "empty").execute().data
    if not claimed:
        raise HTTPException(409, "Locker abhi kisi aur ne le liya, dobara try karo")
    upd = {"locker_id": locker["id"], "assigned_hub_id": chosen_hub, "order_status": "deposited"}
    if o["payment_status"] == "paid":
        upd["pickup_code"] = new_code()
    db.table("orders").update(upd).eq("delivery_id", delivery_id).execute()
    return {
        "locker_id": locker["id"],
        "locker_size": locker["size"],
        "hub_id": chosen_hub,
        "used_backup_hub": chosen_hub != o["hub_id"],
        "qr_active": o["payment_status"] == "paid",
    }


@app.post("/orders/{delivery_id}/pay")
def pay(delivery_id: str):
    o = get_order_row(delivery_id)
    if o["payment_status"] == "paid":
        raise HTTPException(400, "Payment pehle hi ho chuki hai")
    if o["payment_option"] == "pay_later" and o["order_status"] == "placed":
        raise HTTPException(400, "Is order mein payment parcel locker mein aane ke baad hogi")
    upd = {"payment_status": "paid"}
    deposited = o["order_status"] == "deposited"
    if deposited:
        upd["pickup_code"] = new_code()
    db.table("orders").update(upd).eq("delivery_id", delivery_id).execute()
    return {"payment_status": "paid", "qr_active": deposited}


@app.get("/orders/{delivery_id}/pickup")
def pickup(delivery_id: str):
    o = get_order_row(delivery_id)
    if o["payment_status"] != "paid" or not o["pickup_code"]:
        raise HTTPException(403, "Pickup code abhi active nahi hai")
    return {"pickup_code": o["pickup_code"], "hub_id": o["assigned_hub_id"], "locker_id": o["locker_id"]}


@app.post("/orders/{delivery_id}/collect")
def collect(delivery_id: str, body: CollectIn):
    o = get_order_row(delivery_id)
    if o["order_status"] != "deposited" or not o["pickup_code"]:
        raise HTTPException(403, "Is order ka pickup abhi active nahi hai")
    if not secrets.compare_digest(body.pickup_code, o["pickup_code"]):
        raise HTTPException(403, "Galat code")
    db.table("lockers").update({"status": "empty"}).eq("id", o["locker_id"]).execute()
    db.table("orders").update({"order_status": "collected", "pickup_code": None}).eq("delivery_id", delivery_id).execute()
    return {"message": "Locker khul gaya, parcel utha lo"}
