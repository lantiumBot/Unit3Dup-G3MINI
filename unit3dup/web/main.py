from fastapi import FastAPI, APIRouter
from random import randint
from common.torrent_clients import TransmissionClient, QbittorrentClient, RTorrentClient
from common.command import CommandLine
from common.settings import Load,DEFAULT_JSON_PATH

from unit3dup.torrent import View
from unit3dup import pvtTracker
from unit3dup.bot import Bot
from view import custom_console


import uvicorn
import argparse
from random import randint

app = FastAPI()

# Classe che gestisce gli endpoint
class WebApp:
    def __init__(self, config: Load):
        self.router = APIRouter()
        self.numb = randint(0, 100)
        self._setup_routes()

    def _setup_routes(self):
        # Add the endpoints
        self.router.add_api_route("/", self.root, methods=["GET"])
        self.router.add_api_route("/upload/{name}", self.upload, methods=["GET"])

    async def root(self):
        return {"message": f"Hello World {self.numb}"}

    async def upload(self, name: str):



        return {"message": f"Hello {name}, numb is {self.numb}"}


def web():
    web_app = WebApp(config=Load().load_config())
    app.include_router(web_app.router)
    uvicorn.run("unit3dup.web.main:app", host="127.0.0.1", port=8000, reload=True)


