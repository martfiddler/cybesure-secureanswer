from fastapi import FastAPI
from routes_auth import router

app = FastAPI()

app.include_router(router)
