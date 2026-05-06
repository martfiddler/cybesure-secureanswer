from fastapi import FastAPI
from routes_auth import router

app = FastAPI()
@app.get("/")
def root():
  return {"message": "Cybesure API is running"}
app.include_router(router)
